import threading, json
class AppContext:
    _inst = None
    _lock = threading.Lock()
    def __init__(self, cfg_path, poller=None, temp_monitor=None):
import threading, json, os
class AppContext:
    _inst = None
    _lock = threading.Lock()
    def __init__(self, cfg_path, poller=None):
        self.cfg_path = cfg_path
        self._cfg_lock = threading.Lock()
        self._cfg = self._load_cfg()
        self._poller = poller
        self._temp_monitor = temp_monitor
    @classmethod
    def init(cls, cfg_path, poller=None, temp_monitor=None):
        with cls._lock:
            cls._inst = AppContext(cfg_path, poller, temp_monitor); return cls._inst
    @classmethod
    def init(cls, cfg_path, poller=None):
        with cls._lock:
            cls._inst = AppContext(cfg_path, poller); return cls._inst
    @classmethod
    def current(cls): return cls._inst
    def _load_cfg(self):
        with open(self.cfg_path,'r') as f: return json.load(f)
    def load_cfg(self):
        with self._cfg_lock:
            self._cfg = self._load_cfg(); return self._cfg
    def save_cfg(self, cfg):
        with self._cfg_lock:
            with open(self.cfg_path,'w') as f: json.dump(cfg,f,indent=2)
            self._cfg = cfg
    def get_cfg_snapshot(self):
        with self._cfg_lock: import json as _j; return _j.loads(_j.dumps(self._cfg))
    def get_state_snapshot(self):
        if not self._poller: return {}
        return self._poller.get_state()
    def get_temp_snapshot(self):
        if not self._temp_monitor: return {}
        return self._temp_monitor.get_snapshot()
