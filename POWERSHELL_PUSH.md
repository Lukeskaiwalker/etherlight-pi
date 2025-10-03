# Push EtherPi (with temps) to GitHub using PowerShell

# 1) Clone your fork (or the original) locally
git clone https://github.com/<you>/etherlight-pi.git
cd etherlight-pi

# 2) Create a new branch
git checkout -b feature/bmp280-temps

# 3) Drop in the new files (unzipped folder named 'etherlight-pi')
# Copy the contents of the ZIP into the repo root, overwriting existing files.

# 4) Review changes
git status
git diff

# 5) Commit
git add -A
git commit -m "Add BMP280 + CPU temps, UI card, display temps page, wiring/pinout docs, I2C enable in installer"

# 6) Push
git push -u origin feature/bmp280-temps

# 7) Open a Pull Request on GitHub
# Navigate to your repo in the browser; GitHub will offer to create a PR from feature/bmp280-temps.
