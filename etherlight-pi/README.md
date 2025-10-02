# Etherlight Pi (SNMP → LEDs for UniFi Switches)

Two LEDs per port:
- **VLAN LED** (left): steady color from your VLAN→Color map  
- **Link/Speed LED** (right): link status & speed with blink patterns

Speeds (default):
- down = off  
- 10 Mb = gray (steady)  
- 100 Mb = orange (blinking)  
- 1 Gb = green (blinking)  
- 2.5 Gb = cyan (blinking)  
- 10 Gb = blue (blinking)

## Hardware
- Raspberry Pi (Raspberry Pi OS Lite 64-bit recommended)
- WS2812/NeoPixel LEDs (5V) — **2 per port** if you want VLAN + Link indicators  
- Level shifter (3.3V→5V), e.g., 74AHCT125  
- Data pin default: **GPIO18**  
- Common ground with LED PSU

## Quick start (Pi)
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv
cd ~
unzip etherlight-pi.zip
cd etherlight-pi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open `http://<pi-ip>:8080` → set **Switch Host/IP**, **SNMP community**, **Ports**, **LEDs per port**, etc.

## Service mode
```bash
sudo cp service/etherlight.service /etc/systemd/system/
sudo sed -i "s#/home/pi#$(eval echo ~$USER)#" /etc/systemd/system/etherlight.service
sudo systemctl daemon-reload
sudo systemctl enable --now etherlight.service
```

## Config
See `config.json`. Key bits:
- `device.ports.count` → port count (8/16/24/48 …)  
- `device.leds_per_port` → 1 or 2  
- `vlan_colors` → map of `"VLAN": "#RRGGBB"`  
- `link_colors` and `blink` → tweak speed colors and blink timing  

## Notes
- Ensure SNMP v2c is enabled on your UniFi switch.  
- On Windows, you can run the UI/poller with: `pip install Flask pysnmp` (the LED driver auto-falls back to a mock).
