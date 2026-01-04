#!/usr/bin/env python3
import time, threading, socket, math

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = ImageDraw = ImageFont = None

# Try to use luma devices if present
try:
    from luma.core.interface.serial import spi
    from luma.oled.device import ssd1351 as dev_ssd1351
    from luma.lcd.device import st7789 as dev_st7789
    _HAS_LUMA = True
except Exception:
    _HAS_LUMA = False

WHITE = (255, 255, 255)

def _try_font(size: int):
    """Return a truetype font if available, else let Pillow fallback to default."""
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        try:
            # Pillow will use a tiny default if None, but we can also load_default()
            return ImageFont.load_default()
        except Exception:
            return None

# Backwards-compat alias so any call to _font(...) won't crash
def _font(sz: int):
    return _try_font(int(sz))

def _hex_to_rgb(hx: str):
    try:
        h = str(hx).lstrip("#")
        if len(h) == 6:
            return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))
    except Exception:
        pass
    return (16,16,16)

def _mk_device(cfg):
    """
    Create device from cfg. Supports:
      cfg.enabled (bool), cfg.model ('ssd1351_128' / 'st7789_240' / 'auto'),
      cfg.rotation (deg), cfg.width/height, legacy: cfg.driver, cfg.rotate_deg
    Returns (device_or_None, width, height).
    """
    enabled = bool((cfg or {}).get("enabled", True))
    model = str((cfg or {}).get("model") or (cfg or {}).get("driver") or "ssd1351").lower()
    rotation = int((cfg or {}).get('rotation', (cfg or {}).get('rotate_deg', 0)))
    # Map degrees (0/90/180/270) to Luma quarter-turns 0..3; pass-through if already 0..3
    luma_rot = rotation if rotation in (0,1,2,3) else ((rotation // 90) % 4)
    width  = int((cfg or {}).get("width",  240 if "st7789" in model else 128))
    height = int((cfg or {}).get("height", 240 if "st7789" in model else 128))

    if not _HAS_LUMA or not enabled:
        # No hardware: run headless with provided dimensions so we can still render/animate
        return None, width, height

    try:
        serial = spi(
            port=(cfg or {}).get("spi_port", 0),
            device=(cfg or {}).get("spi_device", 0),
            gpio_DC=(cfg or {}).get("gpio_dc", 24),
            gpio_RST=(cfg or {}).get("gpio_rst", 25),
            gpio_CS=(cfg or {}).get("gpio_cs", 8),
        )

        # Normalize model strings
        if "st7789" in model:
            dev = dev_st7789(serial, width=width, height=height, rotate=rotation)
        else:
            # default to ssd1351 128x128
            dev = dev_ssd1351(serial, rotate=luma_rot)

        # Prefer real device-reported size if available
        try:
            width  = getattr(dev, "width", width)
            height = getattr(dev, "height", height)
        except Exception:
            pass
        return dev, width, height
    except Exception:
        return None, width, height


class SmallDisplay:
    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.device, self.W, self.H = _mk_device(self.cfg)
        # timings / options
        self.page_sec = int((self.cfg or {}).get("page_sec", 5))
        self.slide_ms = int((self.cfg or {}).get("slide_ms", 400))
        # runtime
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._queued_splash = True
        self._last_img = None  # for sliding

    def start(self): self._thread.start()
    def stop(self): self._stop.set()
    def queue_splash(self): self._queued_splash = True

    # ---------------- Rendering helpers ----------------

    def _present(self, img):
        """Push a PIL image to the device if present."""
        dev = self.device
        if not dev:
            return  # headless: do nothing
        for attr in ("display", "show", "image"):
            if hasattr(dev, attr):
                getattr(dev, attr)(img)
                return

    def _slide_transition(self, prev_img, next_img):
        """Swipe animate from prev_img -> next_img using slide_ms."""
        ms = max(0, int(self.slide_ms))
        if ms <= 0 or prev_img is None or next_img is None:
            self._present(next_img)
            self._last_img = next_img
            return
        try:
            steps = max(2, min(24, ms // 16))  # ~60fps-ish
            for i in range(steps):
                frac = i / (steps - 1)
                off = int(self.W * frac)
                frame = Image.new("RGB", (self.W, self.H))
                frame.paste(prev_img, (-off, 0))
                frame.paste(next_img, (self.W - off, 0))
                self._present(frame)
            self._present(next_img)
        except Exception:
            self._present(next_img)
        self._last_img = next_img

    def _blank(self, color=(0,0,0)):
        return Image.new("RGB", (self.W, self.H), color)

    # ---------------- Page builders (render to PIL image) ----------------

    def _img_splash(self, text="EtherPi"):
        img = self._blank()
        d = ImageDraw.Draw(img)
        title_f = _try_font(20)
        small_f = _try_font(14)
        # rainbow background
        for y in range(self.H):
            pos = int((y*255)/max(1,self.H-1))
            r,g,b = self._wheel(pos)
            d.line([(0,y),(self.W,y)], fill=(r,g,b))
        d.text((6,6), text, fill=WHITE, font=title_f)
        d.text((6,30), "starting...", fill=WHITE, font=small_f)
        return img

    @staticmethod
    def _wheel(pos):
        if pos < 85:   return (pos*3, 255 - pos*3, 0)
        if pos < 170:  pos -= 85; return (255 - pos*3, 0, pos*3)
        pos -= 170;    return (0, pos*3, 255 - pos*3)

    def _img_home(self, cfg):
        img = self._blank()
        d = ImageDraw.Draw(img)
        title_f = _try_font(22)  # +1
        line_f  = _try_font(17)  # +1
        ip   = self._get_pi_ip()
        host = ((cfg.get("device") or {}).get("switch_host") or "-")
        d.text((4, 4),  "EtherPi",       fill=WHITE, font=title_f)
        d.text((4, 30), f"IP: {ip}",     fill=WHITE, font=line_f)
        d.text((4, 50), f"Switch: {host}", fill=WHITE, font=line_f)
        return img

    def _img_links(self, cfg, st):
        img = self._blank()
        d = ImageDraw.Draw(img)
        title_f = _try_font(22)  # +1
        line_f  = _try_font(17)  # +1
        total = ((cfg.get("device") or {}).get("ports") or {}).get("count", 0)
        up = sum(1 for s in (st or {}).values() if s.get("up"))
        d.text((4, 4),  "Links",            fill=WHITE, font=title_f)
        d.text((4, 30), f"Up {up}/{total}", fill=WHITE, font=line_f)
        return img

    def _img_temps(self, temps):
        img = self._blank()
        d = ImageDraw.Draw(img)
        title_f = _try_font(22)  # +1
        line_f  = _try_font(17)  # +1
        cpu = temps.get("cpu_c")
        ext = temps.get("ext_c")
        d.text((4, 4),  "Temps",                                  fill=WHITE, font=title_f)
        d.text((4, 30), f"CPU: {('--' if cpu is None else f'{cpu:.1f}')} C", fill=WHITE, font=line_f)
        d.text((4, 50), f"EXT: {('--' if ext is None else f'{ext:.1f}')} C", fill=WHITE, font=line_f)
        return img

    def _img_sync(self, cfg):
        img = self._blank()
        d = ImageDraw.Draw(img)
        title_f = _try_font(22)  # +1
        line_f  = _try_font(17)  # +1
        role = ((cfg.get("sync") or {}).get("mode") or "off")
        d.text((4, 4),  "Sync",           fill=WHITE, font=title_f)
        d.text((4, 30), f"Role: {role}",  fill=WHITE, font=line_f)
        return img

    def _img_vlans(self, page_idx, total_pages, items):
        img = self._blank()
        d = ImageDraw.Draw(img)
        # keep VLAN page sizing (no extra +1 bump)
        title_f = _try_font(21)
        line_f  = _try_font(16)
        d.text((4, 4), "VLAN Colors", fill=WHITE, font=title_f)

        y = 28
        for vlan_id, hx in items:
            rgb = _hex_to_rgb(str(hx))
            d.rectangle([(6, y), (24, y+12)], outline=WHITE, fill=rgb)  # swatch
            d.text((30, y-2), f"VLAN {vlan_id}", fill=WHITE, font=line_f)
            y += 18

        pn = f"{page_idx+1}/{total_pages}"
        # crude centering (monospace-ish)
        d.text((self.W//2 - 4*len(pn), self.H-16), pn, fill=WHITE, font=_font(14))
        return img

    def _build_pages(self, cfg):
        pages = ["home", "links", "temps", "sync"]
        vlan_map = (cfg or {}).get("vlan_colors", {}) or {}
        vlan_items = list(vlan_map.items())

        def _key(k):
            try: return (0, int(k))
            except: return (1, str(k))

        vlan_items.sort(key=lambda kv: _key(kv[0]))

        per = 5  # <= hard cap of 5 per user request
        if vlan_items:
            total_pages = (len(vlan_items) + per - 1) // per
            for i in range(total_pages):
                start = i*per
                end = start + per
                pages.append(("vlans", i, total_pages, vlan_items[start:end]))
        return pages

    def _get_pi_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "-"

    # ---------------- Main loop ----------------

    def splash(self, text="EtherPi", ms=1500):
        """Blocking splash (once at boot)."""
        if not Image:
            time.sleep(ms/1000.0); return
        img = self._img_splash(text=text)
        self._present(img)
        time.sleep(ms/1000.0)

    def _render_page(self, spec, cfg, st, temps):
        """Return PIL image for the given page spec."""
        if isinstance(spec, tuple) and spec and spec[0] == "vlans":
            _, idx, total, items = spec
            return self._img_vlans(idx, total, items)
        if spec == "home":
            return self._img_home(cfg)
        if spec == "links":
            return self._img_links(cfg, st)
        if spec == "temps":
            return self._img_temps(temps or {})
        if spec == "sync":
            return self._img_sync(cfg)
        return self._img_home(cfg)

    def _run(self):
        # optional splash
        if self._queued_splash and Image:
            self.splash(ms=int(self.cfg.get("splash_ms", 1500)))
            self._queued_splash = False

        while not self._stop.is_set():
            # pull latest snapshots
            try:
                from app_context import AppContext
                ctx = AppContext.current()
                cfg = ctx.get_cfg_snapshot()
                st = ctx.get_state_snapshot()
                temps = ctx.get_temp_snapshot() or {}
            except Exception:
                cfg, st, temps = {}, {}, {}

            # build and iterate pages
            pages = self._build_pages(cfg)
            for p in pages:
                if self._stop.is_set():
                    break
                try:
                    img = self._render_page(p, cfg, st, temps)
                    self._slide_transition(self._last_img, img)
                except Exception:
                    # best effort render
                    try:
                        self._present(img)  # last built one if any
                    except Exception:
                        pass
                # dwell
                self._stop.wait(max(1, int(self.page_sec)))
