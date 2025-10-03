# EtherPi (SNMP → LEDs + Sync + Display + Temps)

**What it does**
- Maps VLANs & link speeds from a UniFi switch (SNMP) to addressable LEDs
- Optional SPI display with rotating pages (overview, links, temps, sync)
- UDP multicast to sync VLAN colors across multiple Pis
- **NEW:** BMP280 sensor + Pi CPU temperature shown in UI and on display

---

## Hardware
- **Raspberry Pi** (any model with SPI + I2C; Pi 3/4/5 recommended)
- **LED strip**: WS2811 / WS2812 / WS2812B / SK6812 (RGB or RGBW)
- **Small display (optional)**: SSD1351 OLED (128×128) or ST7789 LCD (e.g. 240×240)
- **BMP280** temperature sensor (I2C; typical addr 0x76 or 0x77)
- Proper 5V power for LEDs; common ground with the Pi

### Pin connections
**LEDs (WS281x)**
- Data → **GPIO18** (physical pin 12) — default
- GND  → any Pi GND (pins 6/9/14/20/25/30/34/39)
- 5V   → external 5V supply (do **not** power long strips from Pi 5V)
- Share GND between Pi and LED PSU

**Display (SPI0 default)**
- VCC → **3.3V** (pin 1 or 17)
- GND → **GND**
- SCK/SCL → **GPIO11** (pin 23)
- MOSI/DIN → **GPIO10** (pin 19)
- CS/CE0 → **GPIO8** (pin 24)
- DC/RS → **GPIO24** (pin 18)
- RST → **GPIO25** (pin 22)
- (ST7789) BL/LED → 3.3V (or map to a GPIO if you want control)

**BMP280 (I2C)**
- VCC → **3.3V**
- GND → **GND**
- SDA → **GPIO2 (SDA1)** (pin 3)
- SCL → **GPIO3 (SCL1)** (pin 5)
- Default address `0x76` (some boards use `0x77`)

---

## Software install
```bash
./install.sh
```
The script:
- Installs Python deps (Flask, PySNMP, rpi-ws281x, luma, smbus2, bmp280)
- Enables **SPI** (display) and **I2C** (BMP280)
- Installs a systemd service and starts the app

Open: `http://<pi-ip>:8080/`

---

## Configuration
- Edit `config.json`
  - `device.switch_host` / `device.snmp.community`
  - `device.ports.count` if auto-detect differs
  - `led.*` for type/order/pin/brightness
  - `display.enabled` and model/size
  - `sensors.bmp280.enabled`, `bus`, `address` (`0x76` or `0x77`)

---

## UI
- **Temperatures** card shows: **CPU** and **BMP280** in °C (live).
- **Live Ports** table: ifName, VLAN, speed, link state.
- **VLAN → Color** editor.
- **Detect switch** button fills model + port count.

---

## Troubleshooting
- **LED mmap()/PWM error**: disable onboard audio (`dtparam=audio=off`) and/or run service as root; ensure GND shared.
- **No BMP280 reading**: enable I2C, check wiring & address (0x76 vs 0x77). Use `i2cdetect -y 1` to confirm presence.
- **Display blank**: confirm SPI enabled; check driver/size in `config.json` and wiring.

---

## Service
```bash
journalctl -u etherlight.service -f
sudo systemctl restart etherlight.service
```
# EtherPi (SNMP → LEDs + Sync + Display)
Features:
- LED types: WS2811/WS2812/WS2812B/SK6812 RGB/RGBW
- Rainbow startup animation
- Auto-detect switch model + port count
- VLAN PVID mapping via ifName-derived port numbers; fallback to untagged bitmaps
- UDP multicast sync (master/slave)
- Optional SPI display (SSD1351 / ST7789) with splash + rotating stats
- Dark UI, identify (LEDs/Screen/Both)

## Install
```bash
./install.sh
```
Then open `http://<pi-ip>:8080/`.

## Service
`sudo journalctl -u etherlight.service -f`
