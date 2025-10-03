import asyncio
import re
import threading
from typing import Optional, Dict, Any

# pysnmp >= 7 asyncio API
from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, walk_cmd, get_cmd
)

# ---- Common OIDs we already use ----
OID_SYS_DESCR = '1.3.6.1.2.1.1.1.0'
OID_SYS_NAME   = '1.3.6.1.2.1.1.5.0'
OID_IFNAME    = '1.3.6.1.2.1.31.1.1.1.1'
OID_IFHSPEED  = '1.3.6.1.2.1.31.1.1.1.15'
OID_IFOPER    = '1.3.6.1.2.1.2.2.1.8'
OID_PVID      = '1.3.6.1.2.1.17.7.1.4.5.1.1'
OID_VLAN_CURR_UNTAG = '1.3.6.1.2.1.17.7.1.4.2.1.4'

# ---- Switch temperature candidates (UniFi) ----
# Seen working on some USW models (returns INTEGER Celsius)
UBNT_TEMP_CANDIDATES = [
    '1.3.6.1.4.1.41112.1.1.43.1.8.1.5.1.1',
    '1.3.6.1.4.1.41112.1.1.43.1.15.1.3.1',
]

# ENTITY-SENSOR-MIB (entPhySensor*)
ENT_TYPE  = '1.3.6.1.2.1.99.1.1.1.1'  # entPhySensorType
ENT_SCALE = '1.3.6.1.2.1.99.1.1.1.2'  # entPhySensorScale (ignored here)
ENT_VALUE = '1.3.6.1.2.1.99.1.1.1.4'  # entPhySensorValue

async def _walk(engine, target, community, base_oid) -> Dict[int, Any]:
    out = {}
    async for errInd, errStat, errIdx, varBinds in walk_cmd(
        engine, CommunityData(community), target, ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
        lookupMib=False, lexicographicMode=False
    ):
        if errInd or errStat:
            return out
        for ot in varBinds:
            oid = ot[0].prettyPrint()
            try:
                idx = int(oid.split('.')[-1])
            except Exception:
                continue
            out[idx] = ot[1]
    return out

async def _get_one(engine, target, community, oid: str):
    errInd, errStat, errIdx, varBinds = await get_cmd(
        engine, CommunityData(community), target, ContextData(),
        ObjectType(ObjectIdentity(oid)), lookupMib=False
    )
    if errInd or errStat:
        return None
    for vb in varBinds:
        return vb[1]
    return None

def _octets(v):
    if hasattr(v, 'asOctets'):
        return v.asOctets()
    try:
        return bytes(v)
    except Exception:
        s = v.prettyPrint()
        return bytes.fromhex(s[2:]) if s.startswith('0x') else b''

def _bitmap_ports_msb(mask: bytes):
    ports = set()
    for i, b in enumerate(mask):
        for bit in range(8):
            if b & (1 << (7 - bit)):
                ports.add(i * 8 + bit + 1)
    return ports

def _portnum_from_ifname(name: str) -> Optional[int]:
    m = re.search(r'(\d+)\s*$', name.strip())
    return int(m.group(1)) if m else None

class SnmpPoller(threading.Thread):
    def __init__(self, host, community, interval_sec=5, port_count=16, stop_event=None):
        super().__init__(daemon=True)
        self.host = host
        self.community = community
        self.interval = max(1, int(interval_sec))
        self.port_count = int(port_count)
        self.stop_event = stop_event or threading.Event()
        self.state_lock = threading.Lock()
        self.state: Dict[int, Dict[str, Any]] = {}
        self.model = ""
        self.switch_temp_c: Optional[float] = None

    def get_state(self):
        with self.state_lock:
            return {k: v.copy() for k, v in self.state.items()}

    async def _read_switch_temp(self, eng, tgt) -> Optional[float]:
        # 1) Try UBNT private OIDs first (simple integers in Celsius)
        for oid in UBNT_TEMP_CANDIDATES:
            v = await _get_one(eng, tgt, self.community, oid)
            if v is None:
                continue
            try:
                val = float(str(v))
                if -40.0 < val < 150.0:
                    return val
            except Exception:
                continue

        # 2) Fall back to ENTITY-SENSOR-MIB: any entPhySensorType == degreesCelsius
        try:
            types = await _walk(eng, tgt, self.community, ENT_TYPE)
            values = await _walk(eng, tgt, self.community, ENT_VALUE)
            # entPhySensorType often returns an integer; 8 == degreesCelsius (per the MIB)
            for idx, t in types.items():
                t_s = str(t).lower()
                is_celsius = ('celsius' in t_s)
                if not is_celsius:
                    try:
                        is_celsius = (int(str(t)) == 8)
                    except Exception:
                        is_celsius = False
                if not is_celsius:
                    continue
                raw = values.get(idx)
                if raw is None:
                    continue
                try:
                    val = float(str(raw))
                    if -40.0 < val < 150.0:
                        return val
                except Exception:
                    continue
        except Exception:
            pass

        return None

    async def _poll_once_async(self):
        eng = SnmpEngine()
        tgt = await UdpTransportTarget.create((self.host, 161))

        ifnames_raw = await _walk(eng, tgt, self.community, OID_IFNAME)
        speeds_raw  = await _walk(eng, tgt, self.community, OID_IFHSPEED)
        opers_raw   = await _walk(eng, tgt, self.community, OID_IFOPER)

        ifnames = {i: str(v) for i, v in ifnames_raw.items()}
        speeds  = {i: int(v) for i, v in speeds_raw.items() if str(v).isdigit()}
        opers   = {i: int(v) for i, v in opers_raw.items() if str(v).isdigit()}

        # PVID / VLAN mapping
        pvid_raw = await _walk(eng, tgt, self.community, OID_PVID)
        pvid_by_base = {base: int(v) for base, v in pvid_raw.items() if str(v).isdigit()}
        if not pvid_by_base:
            vlan_untag_raw = await _walk(eng, tgt, self.community, OID_VLAN_CURR_UNTAG)
            base_to_vlan = {}
            for vlan_id, octs in vlan_untag_raw.items():
                for base_port in _bitmap_ports_msb(_octets(octs)):
                    base_to_vlan.setdefault(base_port, vlan_id)
            pvid_by_base = base_to_vlan

        # Choose physical ports (best effort)
        candidates = sorted(ifnames.items(), key=lambda kv: kv[0])
        phys = []
        for ifIndex, name in candidates:
            n = name.lower()
            if any(s in n for s in ['eth', 'port', '/']) or n.isdigit():
                phys.append(ifIndex)
            if len(phys) >= self.port_count:
                break
        if len(phys) < self.port_count:
            phys = [idx for idx, _ in candidates[:self.port_count]]

        new_state: Dict[int, Dict[str, Any]] = {}
        port = 1
        for ifIndex in phys[:self.port_count]:
            base = _portnum_from_ifname(ifnames.get(ifIndex, ""))
            vlan = pvid_by_base.get(base) if base is not None else None
            speed = speeds.get(ifIndex)
            up = (opers.get(ifIndex) == 1)
            new_state[port] = {
                'vlan': vlan,
                'speed': speed,
                'up': up,
                'ifIndex': ifIndex,
                'ifName': ifnames.get(ifIndex, str(ifIndex))
            }
            port += 1

        # Try to read switch temperature (non-fatal if it fails)
        try:
            temp = await self._read_switch_temp(eng, tgt)
        except Exception:
            temp = None

        with self.state_lock:
            self.state = new_state
            self.switch_temp_c = temp

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while not self.stop_event.is_set():
            try:
                loop.run_until_complete(self._poll_once_async())
            except Exception:
                pass
            self.stop_event.wait(self.interval)
        loop.close()

    async def detect_switch(self):
            eng = SnmpEngine()
            tgt = await UdpTransportTarget.create((self.host, 161))

            # sysDescr
            errInd, errStat, errIdx, varBinds = await get_cmd(
                eng, CommunityData(self.community), tgt, ContextData(),
                ObjectType(ObjectIdentity(OID_SYS_DESCR)),
                lookupMib=False
            )
            model = ""
            if not errInd and not errStat:
                for ot in varBinds:
                    model = str(ot[1])

            # sysName (true name)
            errInd2, errStat2, errIdx2, varBinds2 = await get_cmd(
                eng, CommunityData(self.community), tgt, ContextData(),
                ObjectType(ObjectIdentity(OID_SYS_NAME)),
                lookupMib=False
            )
            sysname = ""
            if not errInd2 and not errStat2:
                for ot in varBinds2:
                    sysname = str(ot[1])

            # Guess port count
            ifnames_raw = await _walk(eng, tgt, self.community, OID_IFNAME)
            port_numbers = []
            for s_ in [str(v) for v in ifnames_raw.values()]:
                n = _portnum_from_ifname(s_)
                if n is not None:
                    port_numbers.append(n)
            guessed = max(port_numbers) if port_numbers else len(ifnames_raw)
            return model, guessed, sysname
