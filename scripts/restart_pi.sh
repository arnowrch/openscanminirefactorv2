#!/bin/bash
# Safe singleton restart — run directly on the Pi
# Usage: bash ~/openscan-firmware/scripts/restart_pi.sh
set -e
cd ~/openscan-firmware
source ~/openscan_env/bin/activate

# Graceful stop
pids=$(pgrep -f "uvicorn.*openscan_mini" 2>/dev/null || true)
if [[ -n "$pids" ]]; then
  echo "Stopping: $pids"
  kill $pids 2>/dev/null || true
  sleep 2
  pids_left=$(pgrep -f "uvicorn.*openscan_mini" 2>/dev/null || true)
  [[ -n "$pids_left" ]] && kill -9 $pids_left 2>/dev/null || true
  sleep 1
fi

# Start detached — setsid ensures it survives SSH session end
setsid python -m uvicorn openscan_mini.main:app \
  --host 0.0.0.0 --port 8000 \
  >> ~/openscan-server.log 2>&1 &
disown

# Wait for health
echo -n "Waiting..."
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/health 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    echo " UP (${i}s)"
    break
  fi
  echo -n "."
  sleep 1
done

echo ""
echo "=== Status ==="
pgrep -af "uvicorn" || echo "no uvicorn running!"
curl -s http://localhost:8000/api/v1/health
echo ""
curl -s -o /dev/null -w "camera: %{http_code}\n" http://localhost:8000/api/v1/hardware/camera/
curl -s -o /dev/null -w "motors: %{http_code}\n" http://localhost:8000/api/v1/hardware/motors
