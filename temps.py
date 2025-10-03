#!/usr/bin/env python3
import threading, time, struct, os, subprocess
try:
    from smbus2 import SMBus, i2c_msg
except Exception:
    SMBus = None
    i2c_msg = None

class TempMonitor(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg or {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.values = {"cpu_c": None, "ext_c": None, "bmp280_c": None, "switch_c": None}
        self.bus = None
        self.addr = None
        self.chip_id = None
        self._cal = None  # (T1, T2, T3)
        self._t_fine = 0

    def stop(self):
        self._stop.set()

    def get_snapshot(self):
        with self._lock:
            # mirror ext → bmp280 for backward UI compatibility
            snap = dict(self.values)
            snap["bmp280_c"] = snap.get("ext_c")
            return snap

    # ---------- low-level i2c helpers ----------
    def _open_bus(self):
        if SMBus is None:
            print("[temps] smbus2 not available")
            return False
        s_cfg = (self.cfg.get("sensors") or {}).get("bmp280") or {}
        if not s_cfg.get("enabled", False):
            print("[temps] sensor disabled in config")
            return False
        try:
            busno = int(s_cfg.get("bus", 1))
            addr = s_cfg.get("address", "0x76")
            if isinstance(addr, str) and addr.startswith("0x"):
                addr = int(addr, 16)
            else:
                addr = int(addr)
            self.bus = SMBus(busno)
            self.addr = addr
            return True
        except Exception as e:
            print(f"[temps] open bus failed: {e}")
            return False

    def _wr8(self, reg, val):
        try:
            self.bus.write_byte_data(self.addr, reg, val & 0xFF)
            return True
        except Exception:
            return False

    def _rd8(self, reg):
        try:
            return self.bus.read_byte_data(self.addr, reg)
        except Exception:
            return None

    def _rdN(self, reg, n):
        # try block read, then i2c_rdwr fallback, then per-byte fallback
        try:
            return self.bus.read_i2c_block_data(self.addr, reg, n)
        except Exception:
            pass
        if i2c_msg:
            try:
                wr = i2c_msg.write(self.addr, [reg])
                rd = i2c_msg.read(self.addr, n)
                self.bus.i2c_rdwr(wr, rd)
                return list(bytes(rd))
            except Exception:
                pass
        out = []
        for i in range(n):
            b = self._rd8(reg + i)
            if b is None:
                return None
            out.append(b)
        return out

    def _rdu16le(self, reg):
        b = self._rdN(reg, 2)
        if not b:
            return None
        return struct.unpack("<H", bytes(b))[0]

    def _rds16le(self, reg):
        b = self._rdN(reg, 2)
        if not b:
            return None
        return struct.unpack("<h", bytes(b))[0]

    # ---------- sensor init & read ----------
    def _init_bmx(self):
        """Init BMP280/BME280 in normal mode, read calibration."""
        if self.bus is None and not self._open_bus():
            return False

        # soft reset
        self._wr8(0xE0, 0xB6)
        time.sleep(0.003)

        # chip id (0x58 BMP280, 0x60 BME280)
        cid = self._rd8(0xD0)
        if cid is None:
            print("[temps] failed to read chip id")
            return False
        self.chip_id = cid

        # read temperature calibration (0x88..0x8D), LITTLE-ENDIAN
        T1 = self._rdu16le(0x88)
        T2 = self._rds16le(0x8A)
        T3 = self._rds16le(0x8C)

        # if T1 looks bogus, retry with a full 6-byte block as a fallback
        if T1 in (None, 0):
            blk = self._rdN(0x88, 6) or []
            if len(blk) == 6:
                try:
                    t1b, t2b, t3b = struct.unpack("<Hhh", bytes(blk))
                    if t1b:
                        T1, T2, T3 = t1b, t2b, t3b
                except Exception:
                    pass

        if not T1:
            print("[temps] calibration read failed (T1=0)")
            return False

        self._cal = (int(T1), int(T2), int(T3))

        # config: normal mode, temp oversampling x1 (enough for cabinet temp)
        # ctrl_meas: osrs_t=1 (bits 7:5 = 001), osrs_p=1 (bits 4:2 = 001), mode=normal (bits1:0=11) => 0b001_001_11 = 0x27
        self._wr8(0xF4, 0x27)
        # config filter standby (optional): 500ms standby, filter off => 0xA0
        self._wr8(0xF5, 0xA0)

        print(f"[temps] BMx280 ready @0x{self.addr:02X} id=0x{self.chip_id:02X} "
              f"T1={self._cal[0]} T2={self._cal[1]} T3={self._cal[2]}")
        return True

    def _read_ext_c(self):
        """Read compensated temperature in °C using datasheet formula."""
        if not self._cal:
            if not self._init_bmx():
                return None
        # raw temp (20-bit) @ 0xFA..0xFC
        b = self._rdN(0xFA, 3)
        if not b or len(b) != 3:
            return None
        adc_T = ((b[0] << 12) | (b[1] << 4) | (b[2] >> 4))

        T1, T2, T3 = self._cal
        # datasheet compensation (integer arithmetic)
        var1 = (((adc_T >> 3) - (T1 << 1)) * T2) >> 11
        var2 = (((((adc_T >> 4) - T1) * ((adc_T >> 4) - T1)) >> 12) * T3) >> 14
        self._t_fine = var1 + var2
        T = (self._t_fine * 5 + 128) >> 8  # in 0.01°C
        c = T / 100.0

        # sanity clamp
        if c < -40 or c > 125:
            return None
        return float(c)

    # ---------- CPU temp ----------
    def _read_cpu_c(self):
        # thermal zones first
        for p in ("/sys/class/thermal/thermal_zone0/temp",
                  "/sys/class/thermal/thermal_zone1/temp"):
            try:
                with open(p, "r") as f:
                    v = int(f.read().strip())
                    return v / 1000.0
            except Exception:
                pass
        # vcgencmd fallback
        try:
            out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True).strip()
            if "=" in out:
                v = out.split("=")[1].split("'")[0]
                return float(v)
        except Exception:
            pass
        return None

    # ---------- thread loop ----------
    def run(self):
        # try initialize once up front (quietly continue if it fails)
        self._init_bmx()
        while not self._stop.is_set():
            cpu = self._read_cpu_c()
            ext = self._read_ext_c()
            with self._lock:
                self.values["cpu_c"] = cpu
                self.values["ext_c"] = ext
            self._stop.wait(2.0)
