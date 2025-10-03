#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/7] Updating apt..."
sudo apt update

echo "[2/7] Installing base packages..."
sudo apt install -y python3 python3-pip python3-venv python3-dev build-essential snmp raspi-config i2c-tools || true

echo "[3/7] Enabling SPI (display) and I2C (BMP280)..."
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0 || true
  sudo raspi-config nonint do_i2c 0 || true
fi

echo "[4/7] Installing Python deps..."
pip3 install --break-system-packages -U pip wheel setuptools
pip3 install --break-system-packages -r requirements.txt

echo "[5/7] Udev: ensure 'pi' can access I2C (usually already true)"
sudo adduser "$USER" i2c || true

echo "[6/7] Installing systemd service..."
SVC=/etc/systemd/system/etherlight.service
sudo cp service/etherlight.service "$SVC"
HOMEDIR=$(eval echo ~$USER)
sudo sed -i "s#/home/pi#${HOMEDIR}#g" "$SVC"
sudo systemctl daemon-reload
sudo systemctl enable --now etherlight.service

echo "[7/7] Done. Open http://<pi-ip>:8080/"
