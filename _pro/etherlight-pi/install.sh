#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "[1/6] Updating apt..."
sudo apt update
echo "[2/6] Installing base packages..."
sudo apt install -y python3 python3-pip python3-venv python3-dev build-essential snmp raspi-config || true
echo "[3/6] Enabling SPI for display (if present)..."
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0 || true
fi
echo "[4/6] Installing Python deps..."
pip3 install --break-system-packages -U pip wheel setuptools
pip3 install --break-system-packages -r requirements.txt
echo "[5/6] Installing systemd service..."
SVC=/etc/systemd/system/etherlight.service
sudo cp service/etherlight.service "$SVC"
HOMEDIR=$(eval echo ~$USER)
sudo sed -i "s#/home/pi#${HOMEDIR}#g" "$SVC"
sudo systemctl daemon-reload
sudo systemctl enable --now etherlight.service
echo "[6/6] Done. Open http://<pi-ip>:8080/"
