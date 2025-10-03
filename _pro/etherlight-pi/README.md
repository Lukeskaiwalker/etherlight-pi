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
