import math
try:
    from rpi_ws281x import PixelStrip, Color
    import rpi_ws281x as ws
    _HAS_WS = True
except Exception:
    PixelStrip = None
    Color = None
    _HAS_WS = False

def hex_to_rgb(h):
    h = h.lstrip('#')
    return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

def _strip_type(type_name:str, order:str):
    type_name = (type_name or 'ws2812b').lower()
    order = (order or 'GRB').upper()
    if not _HAS_WS:
        return None
    if type_name in ('ws2812','ws2812b','ws2811','sk6812'):
        order_map = {
            'RGB': ws.WS2811_STRIP_RGB,
            'RBG': ws.WS2811_STRIP_RBG,
            'GRB': ws.WS2811_STRIP_GRB,
            'GBR': ws.WS2811_STRIP_GBR,
            'BRG': ws.WS2811_STRIP_BRG,
            'BGR': ws.WS2811_STRIP_BGR,
        }
        return order_map.get(order, ws.WS2811_STRIP_GRB)
    if type_name in ('sk6812w','sk6812rgbw','sk6812_rgbw','sk6812w-rgbw'):
        om = {
            'RGB': ws.WS2811_STRIP_RGB,'RBG': ws.WS2811_STRIP_RBG,'GRB': ws.WS2811_STRIP_GRB,
            'GBR': ws.WS2811_STRIP_GBR,'BRG': ws.WS2811_STRIP_BRG,'BGR': ws.WS2811_STRIP_BGR,
        }
        return om.get(order, ws.WS2811_STRIP_GRB)
    if type_name in ('sk6812w','sk6812rgbw'):
        return ws.SK6812_STRIP_RGBW
    return ws.WS2811_STRIP_GRB

class _MockStrip:
    def __init__(self, count, pin, brightness=64):
        self._count = count
        self._pixels = [(0,0,0)] * count
        self._brightness = brightness
    def numPixels(self): return self._count
    def setPixelColor(self, i, color):
        if 0 <= i < self._count:
            r = (color >> 16) & 0xFF
            g = (color >> 8)  & 0xFF
            b = (color)       & 0xFF
            self._pixels[i] = (r,g,b)
    def show(self): pass
    def begin(self): pass
    def setBrightness(self, b): self._brightness = b

def _Color(r,g,b):
    return (int(r) << 16) | (int(g) << 8) | int(b)

class LedStrip:
    def __init__(self, port_count, leds_per_port, pin=18, brightness=64, strip_type='ws2812b', color_order='GRB'):
        self.port_count = int(port_count)
        self.leds_per_port = max(1, int(leds_per_port))
        self.total = self.port_count * self.leds_per_port

        if _HAS_WS:
            st = _strip_type(strip_type, color_order)
            self.strip = PixelStrip(self.total, pin, brightness=brightness, strip_type=st)
            self.strip.begin()
            self.strip.setBrightness(brightness)
        else:
            self.strip = _MockStrip(self.total, pin, brightness=brightness)

    def _set_rgb(self, idx, rgb):
        r,g,b = rgb
        if _HAS_WS:
            self.strip.setPixelColor(idx, Color(int(r),int(g),int(b)))
        else:
            self.strip.setPixelColor(idx, _Color(r,g,b))

    def set_port_led(self, port_idx, led_slot, rgb):
        if port_idx < 1 or port_idx > self.port_count:
            return
        base = (port_idx - 1) * self.leds_per_port
        i = base + min(led_slot, self.leds_per_port-1)
        self._set_rgb(i, rgb)

    def set_all_black(self):
        for i in range(self.total):
            self._set_rgb(i, (0,0,0))
        self.show()

    def show(self): self.strip.show()

    
    def rainbow_cycle(self, duration_sec=2.0):
        if self.total <= 0: return
        steps = max(1, int(duration_sec / 0.02))
        for t in range(steps):
            for i in range(self.total):
                pos = (i * 256 // self.total + (t*6)) & 255
                r,g,b = self._wheel(pos)
                self._set_rgb(i, (r,g,b))
            self.show()

    @staticmethod
    def _wheel(pos):
        if pos < 85:   return (pos*3, 255 - pos*3, 0)
        if pos < 170:  pos -= 85; return (255 - pos*3, 0, pos*3)
        pos -= 170;    return (0, pos*3, 255 - pos*3)
        if pos < 85: return (pos*3, 255 - pos*3, 0)
        if pos < 170:
            pos -= 85
            return (255 - pos*3, 0, pos*3)
        pos -= 170
        return (0, pos*3, 255 - pos*3)
