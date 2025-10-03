import threading, json, time

class AppContext:
    _inst = None
    _lock = threading.Lock()

    def __init__(self, cfg_path, poller=None, temp_monitor=None):
        self.cfg_path = cfg_path
        self._cfg_lock = threading.Lock()
        self._cfg = self._load_cfg()
        self._poller = poller
        self._temp_monitor = temp_monitor

        # Runtime flags (not persisted)
        self._rt_lock = threading.Lock()
        self._rt = {
            "identify_active": False,   # global identify pulse state
            "port_flash": {}            # {port: end_epoch}
        }

    @classmethod
    def init(cls, cfg_path, poller=None, temp_monitor=None):
        with cls._lock:
            cls._inst = AppContext(cfg_path, poller, temp_monitor)
            return cls._inst

    @classmethod
    def current(cls):
        return cls._inst

    def _load_cfg(self):
        with open(self.cfg_path, "r") as f:
            return json.load(f)

    def load_cfg(self):
        with self._cfg_lock:
            self._cfg = self._load_cfg()
            return self._cfg

    def save_cfg(self, cfg):
        with self._cfg_lock:
            import tempfile, os, json as _j
            d = os.path.dirname(self.cfg_path) or "."
            fd, tmp = tempfile.mkstemp(prefix="config.", suffix=".json", dir=d)
            with os.fdopen(fd, "w") as f:
                _j.dump(cfg, f, indent=2)
            os.replace(tmp, self.cfg_path)
            self._cfg = cfg

    def get_cfg_snapshot(self):
        with self._cfg_lock:
            import json as _j
            return _j.loads(_j.dumps(self._cfg))

    def get_state_snapshot(self):
        if not self._poller:
            return {}
        return self._poller.get_state()

    def get_temp_snapshot(self):
        if not self._temp_monitor:
            return {}
        return self._temp_monitor.get_snapshot()

    # -------- new runtime helpers --------
    def set_identify(self, active: bool):
        with self._rt_lock:
            self._rt["identify_active"] = bool(active)

    def get_identify(self) -> bool:
        with self._rt_lock:
            return bool(self._rt.get("identify_active", False))

    def flash_port(self, port: int, duration_ms: int = 3000):
        end_t = time.time() + max(0.1, duration_ms / 1000.0)
        with self._rt_lock:
            self._rt.setdefault("port_flash", {})[int(port)] = end_t

    def get_port_flash_snapshot(self):
        now = time.time()
        with self._rt_lock:
            pf = dict(self._rt.get("port_flash", {}))
        # Drop expired (lazy GC)
        expired = [p for p, t in pf.items() if t <= now]
        if expired:
            with self._rt_lock:
                for p in expired:
                    self._rt["port_flash"].pop(p, None)
        return pf
