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
echo "=== 5. Restart server (safe singleton) ==="
# Graceful stop first, hard-kill only if still running after 3s
pids=$(pgrep -f "uvicorn.*openscan_mini" 2>/dev/null || true)
if [[ -n "$pids" ]]; then
  echo "Stopping PIDs: $pids"
  kill $pids 2>/dev/null || true
  for i in $(seq 1 5); do
    sleep 1
    pids_left=$(pgrep -f "uvicorn.*openscan_mini" 2>/dev/null || true)
    [[ -z "$pids_left" ]] && break
    [[ "$i" -eq 5 ]] && kill -9 $pids_left 2>/dev/null || true
  done
fi

# Use setsid so the process survives SSH session end
setsid python -m uvicorn openscan_mini.main:app \
  --host 0.0.0.0 --port 8000 \
  >> ~/openscan-server.log 2>&1 &
disown

echo "Waiting for health check..."
for i in {1..30}; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/health 2>/dev/null || echo "000")
  [[ "$code" == "200" ]] && echo "  UP after ${i}s" && break
  sleep 1
done

echo ""
echo "=== 6. Startup check ==="
grep -i "picamera\|lgpio\|ringlight\|camera\|error\|warning" ~/openscan-server.log | tail -20

echo ""
echo "=== 7. API smoke test ==="
echo -n "health:    "; curl -s http://localhost:8000/api/v1/health | python3 -m json.tool || true
echo -n "camera:    "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/hardware/camera/settings
echo -n "motors:    "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/hardware/motors
echo -n "rotor-end: "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/hardware/motors/rotor/endstop
echo -n "scan-lib:  "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/scan/library
echo -n "tasks:     "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/tasks

echo ""
echo "Done. Tail log: tail -f ~/openscan-server.log"
echo "Web UI: http://192.168.178.135:8000"
