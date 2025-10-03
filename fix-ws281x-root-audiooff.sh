#!/usr/bin/env bash
set -euo pipefail
echo "[1/4] Locate boot config..."
CFG=/boot/firmware/config.txt
[ -f /boot/config.txt ] && CFG=/boot/config.txt
echo " Using: $CFG"
echo "[2/4] Disable onboard audio (frees PWM/PCM for WS281x)..."
sudo sed -i 's/^dtparam=audio=on/# dtparam=audio=on/' "$CFG" || true
if ! grep -q '^dtparam=audio=off' "$CFG"; then
  echo 'dtparam=audio=off' | sudo tee -a "$CFG" >/dev/null
fi
echo "[3/4] Make systemd unit run as root..."
# live unit
if [ -f /etc/systemd/system/etherlight.service ]; then
  sudo sed -i 's/^User=.*/User=root/' /etc/systemd/system/etherlight.service || \
  echo 'User=root' | sudo tee -a /etc/systemd/system/etherlight.service >/dev/null
fi
# repo copy (so future installs keep it)
if [ -f service/etherlight.service ]; then
  sed -i 's/^User=.*/User=root/' service/etherlight.service || \
  echo 'User=root' >> service/etherlight.service
fi

echo "[4/4] Reload/restart service (reboot recommended to fully release audio)..."
sudo systemctl daemon-reload
sudo systemctl restart etherlight.service || true

echo
echo "Done. Please reboot now: sudo reboot"
echo "After reboot, check: journalctl -u etherlight.service -f"
