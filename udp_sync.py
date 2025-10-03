import socket, struct, json, threading, time
class UdpSync:
    def __init__(self, cfg_provider):
        self.cfg_provider = cfg_provider
        self._stop = threading.Event()
        self._thread_tx = None
        self._thread_rx = None
        self._sock_rx = None
    def start(self):
        cfg = self.cfg_provider()
        maddr = cfg['sync']['multicast']; port = int(cfg['sync']['port'])
        self._sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock_rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: self._sock_rx.bind(('', port))
        except Exception: pass
        mreq = struct.pack("=4sl", socket.inet_aton(maddr), socket.INADDR_ANY)
        try: self._sock_rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except Exception: pass
        self._thread_rx = threading.Thread(target=self._rx_loop, daemon=True); self._thread_rx.start()
        self._thread_tx = threading.Thread(target=self._tx_loop, daemon=True); self._thread_tx.start()
    def stop(self): self._stop.set()
    def _tx_loop(self):
        while not self._stop.is_set():
            cfg = self.cfg_provider()
            if cfg['sync']['mode'] == 'master':
                payload = {'type':'vlan_colors','vlan_colors':cfg.get('vlan_colors',{}),'ts':time.time()}
                try: self._send(payload, cfg['sync']['multicast'], cfg['sync']['port'])
                except Exception: pass
            self._stop.wait(1.0)
    def _rx_loop(self):
        while not self._stop.is_set():
            try:
                self._sock_rx.settimeout(1.0)
                data, addr = self._sock_rx.recvfrom(8192)
            except Exception:
                continue
            try: msg = json.loads(data.decode('utf-8','ignore'))
            except Exception: continue
            if msg.get('type') == 'vlan_colors':
                cfg = self.cfg_provider()
                if cfg['sync']['mode'] == 'slave':
                    cfg['vlan_colors'] = msg.get('vlan_colors', {})
                    try:
                        from app_context import AppContext
                        AppContext.current().save_cfg(cfg)
                    except Exception: pass
    def _send(self, obj, maddr, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.sendto(json.dumps(obj).encode('utf-8'), (maddr, int(port)))
        s.close()
