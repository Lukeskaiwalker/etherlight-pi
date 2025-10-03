import time, threading
try:
    from luma.core.interface.serial import spi
    from luma.core.render import canvas
    from luma.oled.device import ssd1351 as dev_ssd1351
    from luma.lcd.device import st7789 as dev_st7789
    from PIL import ImageFont
    _HAS_LUMA = True
except Exception:
    _HAS_LUMA = False

class _MockDisp:
    width = 128; height = 128
    def __init__(self,*a,**kw): pass

def _mk_device(cfg):
    if not _HAS_LUMA or not cfg.get('enabled', False): return _MockDisp()
    serial = spi(port=cfg.get('spi_port',0), device=cfg.get('spi_device',0),
                 gpio_DC=cfg.get('gpio_dc',24), gpio_RST=cfg.get('gpio_rst',25), gpio_CS=cfg.get('gpio_cs',8))
    if (cfg.get('driver','ssd1351')).lower() == 'ssd1351':
        return dev_ssd1351(serial, rotate=cfg.get('rotate_deg',0))
    else:
        return dev_st7789(serial, width=cfg.get('width',240), height=cfg.get('height',240), rotate=cfg.get('rotate_deg',0))

class SmallDisplay:
    def __init__(self, cfg):
        self.cfg = cfg
        self.device = _mk_device(cfg)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._queued_splash = True

    def start(self): self._thread.start()
    def stop(self): self._stop.set()
    def queue_splash(self): self._queued_splash = True

    def splash(self, text="EtherPi", ms=1500):
        if not _HAS_LUMA: return
        with canvas(self.device) as draw:
            H = getattr(self.device, 'height', 128)
            for y in range(H):
                pos = int((y*255)/max(1,H-1))
                r,g,b = self._wheel(pos)
                draw.line([(0,y),(200,y)], fill=(r,g,b))
            try: fnt = ImageFont.truetype("DejaVuSans.ttf", 16)
            except Exception: fnt = None
            draw.text((6,6), text, fill="white", font=fnt)
            draw.text((6,26), "starting...", fill="white", font=fnt)
        time.sleep(ms/1000.0)

    def _run(self):
        if self._queued_splash:
            self.splash(ms=self.cfg.get('splash_ms',1500))
            self._queued_splash = False
        idx = 0
        while not self._stop.is_set():
            with canvas(self.device) as draw:
                from app_context import AppContext
                ctx = AppContext.current()
                st = ctx.get_state_snapshot()
                cfg = ctx.get_cfg_snapshot()
                if idx == 0:
                    draw.text((4,4), "EtherPi", fill="white")
                    draw.text((4,24), f"Switch {cfg.get('device',{}).get('switch_host','?')}", fill="white")
                elif idx == 1:
                    up = sum(1 for s in st.values() if s.get('up'))
                    total = cfg.get('device',{}).get('ports',{}).get('count',0)
                    draw.text((4,4), "Links", fill="white")
                    draw.text((4,24), f"Up {up}/{total}", fill="white")
                else:
                    role = cfg.get('sync',{}).get('mode','off')
                    draw.text((4,4), "Sync", fill="white")
                    draw.text((4,24), f"Role: {role}", fill="white")
            idx = (idx + 1) % 3
            self._stop.wait(2.0)

    @staticmethod
    def _wheel(pos):
        if pos < 85: return (pos*3, 255 - pos*3, 0)
        if pos < 170:
            pos -= 85; return (255 - pos*3, 0, pos*3)
        pos -= 170; return (0, pos*3, 255 - pos*3)
