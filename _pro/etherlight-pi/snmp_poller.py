import asyncio, re, threading
from pysnmp.hlapi.v3arch.asyncio import (SnmpEngine, CommunityData, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, walk_cmd, get_cmd)

OID_SYS_DESCR = '1.3.6.1.2.1.1.1.0'
OID_IFNAME    = '1.3.6.1.2.1.31.1.1.1.1'
OID_IFHSPEED  = '1.3.6.1.2.1.31.1.1.1.15'
OID_IFOPER    = '1.3.6.1.2.1.2.2.1.8'
OID_PVID      = '1.3.6.1.2.1.17.7.1.4.5.1.1'
OID_VLAN_CURR_UNTAG = '1.3.6.1.2.1.17.7.1.4.2.1.4'

async def _walk(engine, target, community, base_oid):
    out = {}
    async for errInd, errStat, errIdx, varBinds in walk_cmd(
        engine, CommunityData(community), target, ContextData(),
        ObjectType(ObjectIdentity(base_oid)), lookupMib=False, lexicographicMode=False):
        if errInd or errStat: return out
        for ot in varBinds:
            oid = ot[0].prettyPrint()
            idx = int(oid.split('.')[-1]); out[idx] = ot[1]
    return out

def _octets(v):
    if hasattr(v,'asOctets'): return v.asOctets()
    try: return bytes(v)
    except Exception:
        s = v.prettyPrint(); return bytes.fromhex(s[2:]) if s.startswith('0x') else b''

def _bitmap_ports_msb(mask: bytes):
    ports = set()
    for i,b in enumerate(mask):
        for bit in range(8):
            if b & (1 << (7-bit)):
                ports.add(i*8 + bit + 1)
    return ports

def _portnum_from_ifname(name: str):
    m = re.search(r'(\d+)\s*$', name.strip()); return int(m.group(1)) if m else None

class SnmpPoller(threading.Thread):
    def __init__(self, host, community, interval_sec=5, port_count=16, stop_event=None):
        super().__init__(daemon=True)
        self.host = host; self.community = community
        self.interval = max(1, int(interval_sec)); self.port_count = port_count
        self.stop_event = stop_event or threading.Event()
        self.state_lock = threading.Lock(); self.state = {}; self.model = ""

    def get_state(self):
        with self.state_lock:
            return {k:v.copy() for k,v in self.state.items()}

    async def _poll_once_async(self):
        eng = SnmpEngine(); tgt = await UdpTransportTarget.create((self.host,161))
        ifnames_raw = await _walk(eng, tgt, self.community, OID_IFNAME)
        speeds_raw  = await _walk(eng, tgt, self.community, OID_IFHSPEED)
        opers_raw   = await _walk(eng, tgt, self.community, OID_IFOPER)
        ifnames = {i:str(v) for i,v in ifnames_raw.items()}
        speeds  = {i:int(v) for i,v in speeds_raw.items() if str(v).isdigit()}
        opers   = {i:int(v) for i,v in opers_raw.items() if str(v).isdigit()}
        pvid_raw = await _walk(eng, tgt, self.community, OID_PVID)
        pvid_by_base = {base:int(v) for base,v in pvid_raw.items() if str(v).isdigit()}
        if not pvid_by_base:
            vlan_untag_raw = await _walk(eng, tgt, self.community, OID_VLAN_CURR_UNTAG)
            base_to_vlan = {}
            for vlan_id, octs in vlan_untag_raw.items():
                for base_port in _bitmap_ports_msb(_octets(octs)):
                    base_to_vlan.setdefault(base_port, vlan_id)
            pvid_by_base = base_to_vlan
        candidates = sorted(ifnames.items(), key=lambda kv: kv[0])
        phys = []
        for ifIndex, name in candidates:
            n = name.lower()
            if any(s in n for s in ['eth','port','/']) or n.isdigit():
                phys.append(ifIndex)
            if len(phys) >= self.port_count: break
        if len(phys) < self.port_count:
            phys = [idx for idx,_ in candidates[:self.port_count]]
        new_state = {}
        port = 1
        for ifIndex in phys[:self.port_count]:
            base = _portnum_from_ifname(ifnames.get(ifIndex,""))
            vlan = pvid_by_base.get(base) if base is not None else None
            speed = speeds.get(ifIndex); up = (opers.get(ifIndex) == 1)
            new_state[port] = {'vlan': vlan, 'speed': speed, 'up': up, 'ifIndex': ifIndex, 'ifName': ifnames.get(ifIndex, str(ifIndex))}
            port += 1
        with self.state_lock: self.state = new_state

    def run(self):
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        while not self.stop_event.is_set():
            try: loop.run_until_complete(self._poll_once_async())
            except Exception: pass
            self.stop_event.wait(self.interval)
        loop.close()

    async def detect_switch(self):
        eng = SnmpEngine(); tgt = await UdpTransportTarget.create((self.host,161))
        errInd, errStat, errIdx, varBinds = await get_cmd(eng, CommunityData(self.community), tgt, ContextData(),
                                                          ObjectType(ObjectIdentity(OID_SYS_DESCR)), lookupMib=False)
        model = ""
        if not errInd and not errStat:
            for ot in varBinds: model = str(ot[1])
        ifnames_raw = await _walk(eng, tgt, self.community, OID_IFNAME)
        port_numbers = []
        for s in [str(v) for v in ifnames_raw.values()]:
            n = _portnum_from_ifname(s)
            if n is not None: port_numbers.append(n)
        guessed = max(port_numbers) if port_numbers else len(ifnames_raw)
        return model, guessed
