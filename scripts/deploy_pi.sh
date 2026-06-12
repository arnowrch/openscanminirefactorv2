#!/bin/bash
# Deploy openscan-firmware to Pi (run this in VS Code terminal via SSH remote)
# Pi: master@192.168.178.135 | venv: ~/openscan_env | server: ~/openscan-firmware
set -e

echo "=== 1. Pull latest code ==="
cd ~/openscan-firmware
git pull

echo ""
echo "=== 2. System packages (apt) ==="
# python3-picamera2 and python3-lgpio are apt-only — cannot be pip-installed
sudo apt install -y \
  python3-picamera2 \
  python3-lgpio \
  swig \
  python3-pillow

echo ""
echo "=== 3. Recreate venv with --system-site-packages ==="
# Required so the venv sees apt-installed picamera2, lgpio, and pillow
rm -rf ~/openscan_env
python3 -m venv --system-site-packages ~/openscan_env

echo ""
echo "=== 4. Install pip packages into venv ==="
source ~/openscan_env/bin/activate
pip install --quiet fastapi "uvicorn[standard]" pydantic aiofiles
# editable install so openscan_mini is importable after venv recreate
pip install --quiet -e ~/openscan-firmware/firmware

echo ""
echo "=== 4b. Optional: opencv for focus stacking ==="
# Install headless opencv if not already present (non-fatal if it fails)
pip install --quiet opencv-python-headless || echo "opencv skipped (ok)"

echo ""
echo "=== 5. Restart server ==="
pkill -f "uvicorn.*openscan" 2>/dev/null || true
sleep 1
nohup python -m uvicorn openscan_mini.main:app \
  --host 0.0.0.0 --port 8000 \
  >> ~/openscan-server.log 2>&1 &

echo "Waiting for startup..."
sleep 4

echo ""
echo "=== 6. Startup check ==="
grep -i "picamera\|lgpio\|ringlight\|camera\|error" ~/openscan-server.log | tail -15

echo ""
echo "=== 7. API smoke test ==="
curl -s http://localhost:8000/api/v1/hardware/camera/ | python3 -m json.tool || true
echo ""
curl -s http://localhost:8000/api/v1/hardware/lights/ | python3 -m json.tool || true

echo ""
echo "Done. Tail log: tail -f ~/openscan-server.log"
echo "Web UI: http://192.168.178.135:8000"
