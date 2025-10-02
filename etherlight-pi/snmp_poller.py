from pysnmp.hlapi import *
import threading

OID_IFNAME      = ObjectIdentity('IF-MIB', 'ifName')
OID_IFHSPEED    = ObjectIdentity('IF-MIB', 'ifHighSpeed')
OID_IFOPER      = ObjectIdentity('IF-MIB', 'ifOperStatus')
OID_PVID        = ObjectIdentity('Q-BRIDGE-MIB', 'dot1qPvid')

class SnmpPoller(threading.Thread):
    def __init__(self, host, community, interval_sec=5, port_count=16, stop_event=None):
        super().__init__(daemon=True)
        self.host = host
        self.community = community
        self.interval = max(1, int(interval_sec))
        self.port_count = port_count
        self.stop_event = stop_event or threading.Event()
        self.state_lock = threading.Lock()
        self.state = {}

    def run(self):
        while not self.stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                pass
            self.stop_event.wait(self.interval)

    def get_state(self):
        with self.state_lock:
            return {k:v.copy() for k,v in self.state.items()}

    def _walk(self, oid):
        handler = nextCmd(SnmpEngine(),
            CommunityData(self.community, mpModel=1),
            UdpTransportTarget((self.host, 161), timeout=1.5, retries=1),
            ContextData(),
            ObjectType(oid),
            lexicographicMode=False)
        for (errInd, errStat, errIdx, varBinds) in handler:
            if errInd or errStat:
                return []
            for varBind in varBinds:
                yield varBind

    def _poll_once(self):
        ifnames = {}
        for vb in self._walk(OID_IFNAME):
            oid, val = vb
            ifIndex = int(oid.prettyPrint().split('.')[-1])
            ifnames[ifIndex] = str(val)

        pvids = {}
        for vb in self._walk(OID_PVID):
            oid, val = vb
            ifIndex = int(oid.prettyPrint().split('.')[-1])
            try: pvids[ifIndex] = int(val)
            except: pass

        speeds = {}
        for vb in self._walk(OID_IFHSPEED):
            oid, val = vb
            ifIndex = int(oid.prettyPrint().split('.')[-1])
            try: speeds[ifIndex] = int(val)
            except: pass

        opers = {}
        for vb in self._walk(OID_IFOPER):
            oid, val = vb
            ifIndex = int(oid.prettyPrint().split('.')[-1])
            try: opers[ifIndex] = int(val)==1
            except: pass

        candidates = sorted(ifnames.items(), key=lambda kv: kv[0])
        phys = []
        for idx, name in candidates:
            n = name.lower()
            if any(s in n for s in ['eth', 'port']):
                phys.append(idx)
            elif n.isdigit():
                phys.append(idx)
            if len(phys) >= self.port_count:
                break
        if len(phys) < self.port_count:
            phys = [idx for idx,_ in candidates[:self.port_count]]

        new_state = {}
        port_number = 1
        for ifIndex in phys[:self.port_count]:
            vlan = pvids.get(ifIndex)
            speed = speeds.get(ifIndex)
            up = opers.get(ifIndex, False)
            new_state[port_number] = {'vlan': vlan, 'speed': speed, 'up': up, 'ifIndex': ifIndex, 'ifName': ifnames.get(ifIndex, str(ifIndex))}
            port_number += 1

        with self.state_lock:
            self.state = new_state
