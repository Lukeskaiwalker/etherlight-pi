import threading, time

class TempMonitor(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg or {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.values = {"cpu_c": None, "bmp280_c": None}
        self._bmp = None
        self._bmp_exc = None

    def stop(self):
        self._stop.set()

    def get_snapshot(self):
        with self._lock:
            return dict(self.values)

    def _read_cpu_temp(self):
        # Try thermal zones
        paths = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/thermal/thermal_zone1/temp"
        ]
        for p in paths:
            try:
                with open(p, "r") as f:
                    v = f.read().strip()
                    milli = int(v)
                    return milli / 1000.0
            except Exception:
                continue
        # Fallback to vcgencmd
        try:
            import subprocess
            out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True).strip()
            if "=" in out:
                v = out.split("=")[1].split("'")[0]
                return float(v)
        except Exception:
            pass
        return None

    def _ensure_bmp(self):
        if self._bmp is not None or self._bmp_exc is not None:
            return
        try:
            from smbus2 import SMBus
            from bmp280 import BMP280
            s_cfg = (self.cfg.get("sensors") or {}).get("bmp280") or {}
            if not s_cfg.get("enabled", False):
                self._bmp_exc = RuntimeError("BMP280 disabled")
                return
            bus = int(s_cfg.get("bus", 1))
            addr = s_cfg.get("address", "0x76")
            if isinstance(addr, str) and addr.startswith("0x"):
                addr = int(addr, 16)
            else:
                addr = int(addr)
            i2c = SMBus(bus)
            self._bmp = BMP280(i2c_dev=i2c, i2c_addr=addr)
        except Exception as e:
            self._bmp_exc = e

    def _read_bmp(self):
        self._ensure_bmp()
        if self._bmp is None:
            return None
        try:
            t = self._bmp.get_temperature()
            if t is None or t < -40 or t > 125:
                return None
            return float(t)
        except Exception:
            return None

    def run(self):
        while not self._stop.is_set():
            cpu = self._read_cpu_temp()
            bmp = self._read_bmp()
            with self._lock:
                self.values["cpu_c"] = cpu
                self.values["bmp280_c"] = bmp
            self._stop.wait(2.0)
