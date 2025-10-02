import threading

def _hex_to_rgb(h):
    h = h.lstrip('#')
    return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))

def hex_to_rgb(h):
    return _hex_to_rgb(h)

class _MockStrip:
    def __init__(self, count, pin, brightness=64):
        self._count = count
        self._pixels = [(0,0,0)] * count
        self._brightness = brightness
    def numPixels(self):
        return self._count
    def setPixelColor(self, i, color):
        r = (color >> 16) & 0xFF
        g = (color >> 8)  & 0xFF
        b = (color)       & 0xFF
        if 0 <= i < self._count:
            self._pixels[i] = (r,g,b)
    def show(self): pass
    def begin(self): pass
    def setBrightness(self, b): self._brightness = b

def _Color(r,g,b):
    return (r << 16) | (g << 8) | b

class LedStrip:
    def __init__(self, port_count, leds_per_port, pin, brightness=64):
        self.port_count = port_count
        self.leds_per_port = max(1, int(leds_per_port))
        self.total = self.port_count * self.leds_per_port

        try:
            from rpi_ws281x import PixelStrip, Color
            global _Color
            _Color = Color
            self.strip = PixelStrip(self.total, pin, brightness=brightness)
            self.strip.begin()
            self.strip.setBrightness(brightness)
            self._real = True
        except Exception:
            self.strip = _MockStrip(self.total, pin, brightness=brightness)
            self._real = False

    def _set_rgb(self, idx, rgb):
        r,g,b = rgb
        self.strip.setPixelColor(idx, _Color(int(r),int(g),int(b)))

    def set_port_led(self, port_idx, led_slot, rgb):
        if port_idx < 1 or port_idx > self.port_count:
            return
        base = (port_idx - 1) * self.leds_per_port
        i = base + min(led_slot, self.leds_per_port-1)
        self._set_rgb(i, rgb)

    def set_all_black(self):
        for i in range(self.total):
            self._set_rgb(i, (0,0,0))
        self.strip.show()

    def show(self):
        self.strip.show()

    @staticmethod
    def hex_to_rgb(h): return _hex_to_rgb(h)
