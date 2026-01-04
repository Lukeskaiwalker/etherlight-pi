#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

TARGET_USER="${SUDO_USER:-$USER}"

echo "[1/9] Updating apt..."
sudo apt update

echo "[2/9] Installing base packages..."
sudo apt install -y python3 python3-pip python3-venv python3-dev build-essential snmp i2c-tools scons
sudo apt install -y raspi-config || true

echo "[3/9] Enabling SPI (display) and I2C (BMP280)..."
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0 || true
  sudo raspi-config nonint do_i2c 0 || true
else
  echo "  raspi-config not found; enable SPI/I2C manually."
fi

echo "[4/9] Creating virtualenv..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

echo "[5/9] Installing Python deps..."
./.venv/bin/python -m pip install -U pip wheel setuptools
./.venv/bin/pip install -r requirements.txt

echo "[6/9] Udev: ensure user can access I2C (usually already true)"
sudo adduser "$TARGET_USER" i2c || true

echo "[7/9] Installing systemd service..."
SVC=/etc/systemd/system/etherlight.service
sudo cp service/etherlight.service "$SVC"
sudo sed -i "s|__BASE_DIR__|${BASE_DIR}|g" "$SVC"
sudo systemctl daemon-reload
sudo systemctl enable --now etherlight.service

DISABLE_AUDIO="${DISABLE_AUDIO:-1}"
if [ "$DISABLE_AUDIO" = "1" ]; then
  echo "[8/9] Disabling onboard audio (frees PWM/PCM for WS281x)..."
  CFG=/boot/firmware/config.txt
  [ -f /boot/config.txt ] && CFG=/boot/config.txt
  sudo sed -i 's/^dtparam=audio=on/# dtparam=audio=on/' "$CFG" || true
  if ! grep -q '^dtparam=audio=off' "$CFG"; then
    echo 'dtparam=audio=off' | sudo tee -a "$CFG" >/dev/null
  fi
  NEED_REBOOT=1
else
  echo "[8/9] Skipping audio disable (DISABLE_AUDIO=0)."
fi

echo "[9/9] Done. Open http://<pi-ip>:8080/"
if [ "${NEED_REBOOT:-0}" = "1" ]; then
  echo "Reboot recommended to fully release audio: sudo reboot"
fi
