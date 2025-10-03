try:
    from rpi_ws281x import PixelStrip, Color
    import rpi_ws281x as ws
    _HAS_WS = True
except Exception:
    PixelStrip = None
    Color = None
    _HAS_WS = False

def hex_to_rgb(h: str):
    h = h.lstrip("#")
    if len(h) >= 6:
        return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))
    return (0,0,0)

def rgb_to_rgbw(r:int,g:int,b:int):
    """
    Simple extraction: put the shared component into W, reduce RGB by that amount.
    Keeps saturated colours intact (W=0), gives neutral whites/greys to the W LED.
    """
    w = min(r, g, b)
    return (r - w, g - w, b - w, w)

def _strip_type(type_name:str, order:str):
    if not _HAS_WS:
        return None
    t = (type_name or "ws2812b").lower()
    o = (order or "GRB").upper()
    # RGBW family
    if t in ("sk6812w","sk6812_rgbw","sk6812rgbw","sk6812"):
        # Accept 4-letter orders like GRBW/RGBW/GBRW/…
        const = f"SK6812_STRIP_{o}"
        return getattr(ws, const, getattr(ws, "SK6812_STRIP_GRBW", ws.WS2811_STRIP_GRB))
    # RGB family
    # Accept 3-letter orders
    const = f"WS2811_STRIP_{o}"
    return getattr(ws, const, ws.WS2811_STRIP_GRB)

class _MockStrip:
    def __init__(self, count, pin, brightness=64, **_):
        self._count = count
        self._pixels = [(0,0,0,0)] * count
        self._brightness = brightness
    def numPixels(self): return self._count
    def setPixelColor(self, i, color):
        pass
    def show(self): pass
    def begin(self): pass
    def setBrightness(self, b): self._brightness = b

class LedStrip:
    def __init__(self, port_count, leds_per_port, pin=18, brightness=64, strip_type='ws2812b', color_order='GRB'):
        self.port_count   = int(port_count)
        self.leds_per_port= max(1, int(leds_per_port))
        self.total        = self.port_count * self.leds_per_port
        self._order       = (color_order or "GRB").upper()
        self._is_rgbw     = ("W" in self._order) or (strip_type and "w" in str(strip_type).lower())

        if _HAS_WS:
            st = _strip_type(strip_type, color_order)
            self.strip = PixelStrip(self.total, pin, brightness=brightness, strip_type=st)
            self.strip.begin()
            self.strip.setBrightness(brightness)
        else:
            self.strip = _MockStrip(self.total, pin, brightness=brightness)

    def _set_color(self, idx:int, rgba):
        """rgba: (r,g,b) or (r,g,b,w)"""
        if idx < 0 or idx >= self.total: return
        if _HAS_WS:
            if self._is_rgbw:
                if len(rgba) == 3:
                    r,g,b = rgba
                    r,g,b,w = rgb_to_rgbw(int(r),int(g),int(b))
                else:
                    r,g,b,w = rgba
                self.strip.setPixelColor(idx, Color(int(r),int(g),int(b),int(w)))
            else:
                r,g,b = (rgba + (0,))[:3]
                self.strip.setPixelColor(idx, Color(int(r),int(g),int(b)))
        else:
            # mock: do nothing visible
            pass

    # Back-compat with existing app.py identify() which calls _set_rgb(...)
    def _set_rgb(self, idx:int, rgb):
        self._set_color(idx, rgb)

    def set_port_led(self, port_idx:int, led_slot:int, rgb_tuple):
        base = (port_idx - 1) * self.leds_per_port
        i = base + min(max(led_slot,0), self.leds_per_port-1)
        self._set_color(i, rgb_tuple)

    def set_all_black(self):
        for i in range(self.total):
            self._set_color(i, (0,0,0,0))
        self.show()

    def show(self): self.strip.show()

    def rainbow_cycle(self, duration_sec=1.5):
        if self.total <= 0: return
        steps = max(1, int(duration_sec / 0.02))
        for t in range(steps):
            for i in range(self.total):
                pos = (i * 256 // max(1,self.total-1) + (t*6)) & 255
                r,g,b = self._wheel(pos)
                if self._is_rgbw:
                    r,g,b,w = rgb_to_rgbw(r,g,b)
                    self._set_color(i, (r,g,b,w))
                else:
                    self._set_color(i, (r,g,b))
            self.show()

    @staticmethod
    def _wheel(pos:int):
        # standard RGB wheel
        if pos < 85:    return (pos*3, 255 - pos*3, 0)
        if pos < 170:   pos -= 85;  return (255 - pos*3, 0, pos*3)
        pos -= 170;     return (0, pos*3, 255 - pos*3)
