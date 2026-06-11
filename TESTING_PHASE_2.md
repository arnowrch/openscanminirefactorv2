# Phase 2.1 Testing — Motor Control

**Hardware**: Raspberry Pi 4 @ 192.168.178.135  
**Status**: Code complete, ready for hardware testing

---

## Step 1: Install Dependencies on Pi

SSH in:
```bash
ssh pi@openscanmini.local
# or: ssh pi@192.168.178.135
```

Install firmware & dependencies:
```bash
cd ~
git clone https://github.com/your-user/openscan-mini.git openscan-firmware
cd openscan-firmware/firmware

# Create virtual environment
python3 -m venv ~/openscan_env
source ~/openscan_env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Verify installation
python3 -c "import fastapi, gpiozero; print('✓ Dependencies OK')"
```

Copy hardware config:
```bash
mkdir -p ~/.openscan
cp ../configs/hardware_greenshield.json ~/.openscan/hardware.json

# Verify config
cat ~/.openscan/hardware.json | head -20
```

---

## Step 2: Start Firmware Server

```bash
cd ~/openscan-firmware/firmware
source ~/openscan_env/bin/activate

# Start server (will print logs)
python3 -m openscan_mini.main

# Expected output:
# INFO - Logging initialized
# INFO - Loading hardware config from ~/.openscan/hardware.json
# INFO - Hardware config loaded successfully: OpenScan Mini v2 (Greenshield)
# INFO - Motor controller initialized
# INFO - FastAPI application ready
# INFO - Listening on http://0.0.0.0:8000
```

Keep this terminal open.

---

## Step 3: Test API in Another Terminal

SSH into a new terminal on the same Pi (or from your dev machine):

### 3a. Health Check
```bash
curl http://192.168.178.135:8000/api/v1/status

# Response:
# {
#   "device": {
#     "name": "OpenScan Mini v2 (Greenshield)",
#     "model": "mini",
#     "shield": "greenshield",
#     "pi_model": "Raspberry Pi 4",
#     "os": "Raspberry Pi OS (Bookworm) 64-bit"
#   },
#   "firmware": {
#     "version": "0.2.0-dev",
#     "api_version": "v1"
#   },
#   "status": "ready",
#   "motors": {
#     "rotor": {
#       "position": 0.0,
#       "min_angle": 0,
#       "max_angle": 145
#     },
#     "turntable": {
#       "position": 0.0,
#       "min_angle": 0,
#       "max_angle": 360
#     }
#   },
#   "camera": {...},
#   "ringlight": {...}
# }
```

### 3b. Get Motor Status
```bash
curl http://192.168.178.135:8000/api/v1/hardware/motors

# Response:
# {
#   "motors": {
#     "rotor": {
#       "motor_id": "rotor",
#       "current_angle": 0.0,
#       "is_moving": false,
#       "min_angle": 0,
#       "max_angle": 145,
#       ...
#     },
#     "turntable": {
#       "motor_id": "turntable",
#       "current_angle": 0.0,
#       "is_moving": false,
#       "min_angle": 0,
#       "max_angle": 360,
#       ...
#     }
#   },
#   "timestamp": 1718092800.123
# }
```

### 3c. Move Rotor (CAREFULLY!)

⚠️ **SAFETY FIRST**: Make sure there's no obstruction!

```bash
# Move rotor to 45°
curl -X POST http://192.168.178.135:8000/api/v1/hardware/motors/rotor/move \
  -H "Content-Type: application/json" \
  -d '{"angle": 45.0}'

# Response:
# {
#   "motor_id": "rotor",
#   "current_angle": 45.0,
#   "is_moving": false,
#   "target_angle": 45.0,
#   ...
# }
```

### 3d. Move Turntable

```bash
# Move turntable to 180°
curl -X POST http://192.168.178.135:8000/api/v1/hardware/motors/turntable/move \
  -H "Content-Type: application/json" \
  -d '{"angle": 180.0}'
```

### 3e. Move Both Motors Simultaneously

```bash
# Rotor to 30°, Turntable to 90°
curl -X POST "http://192.168.178.135:8000/api/v1/hardware/motors/both/move?rotor_angle=30&turntable_angle=90&synchronous=true"
```

### 3f. Reset to Home (0°)

```bash
curl -X POST http://192.168.178.135:8000/api/v1/hardware/motors/reset

# Response:
# {
#   "status": "ok",
#   "message": "All motors reset to 0°"
# }
```

---

## Step 4: Expected Behavior

### Simulation Mode (No GPIO Hardware)
If GPIO is not available (or on dev machine):
- Motors move in simulation
- No physical movement
- Angles update correctly
- API responds immediately

### Real Hardware Mode (Pi 4)
If GPIO is properly configured:
- You should **hear stepper motor buzzing**
- Movement should be smooth (acceleration ramp)
- Current angle updates in real-time
- Movement takes ~0.5-1 second per ~45° (depends on acceleration)

**If you don't hear motor sounds**:
1. ✓ Check GPIO pins are correctly wired
2. ✓ Check `12V power supply` is connected to motors
3. ✓ Verify config pins in `hardware_greenshield.json`
4. ✓ Run: `gpioinfo gpiochip0 | grep -A 2 "GPIO 27"` (check pin 27 = rotor step)

---

## Step 5: Test with Postman (Optional)

1. Download [Postman](https://www.postman.com/downloads/)
2. Import collection (or create manually):

```
Base URL: http://192.168.178.135:8000

Requests:
  GET    /api/v1/status
  GET    /api/v1/hardware/motors
  POST   /api/v1/hardware/motors/rotor/move         [{"angle": 45}]
  POST   /api/v1/hardware/motors/turntable/move     [{"angle": 180}]
  POST   /api/v1/hardware/motors/both/move          [rotor_angle=45&turntable_angle=90]
  POST   /api/v1/hardware/motors/reset
```

---

## Step 6: Running Tests

On dev machine (with mock GPIO):

```bash
cd firmware

# Install test dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio

# Run all motor tests
pytest tests/test_motor_control.py -v

# Expected output:
# tests/test_motor_control.py::TestMotorController::test_initialization PASSED
# tests/test_motor_control.py::TestMotorController::test_get_status PASSED
# tests/test_motor_control.py::TestMotorController::test_angle_validation PASSED
# tests/test_motor_control.py::TestMotorController::test_move_to_angle PASSED
# ...
# ======================== 15 passed in 0.34s ========================
```

---

## Step 7: Troubleshooting

| Issue | Debug |
|-------|-------|
| **404: Endpoint not found** | Check URL spelling, base path is `/api/v1/` |
| **Connection refused** | Is server running? Check IP & port 8000 |
| **Motors don't move** | Check GPIO wiring, verify config pins |
| **Permission denied /dev/gpiochip** | Run `sudo usermod -a -G gpio pi` |
| **Import error: gpiozero** | `pip install gpiozero` in virtual env |
| **JSON parse error** | Check `-H "Content-Type: application/json"` header |

---

## Step 8: What's Next

Once motors work:

### ✅ Phase 2.2 (Next)
- [ ] Ringlight PWM control (GPIO 26, 19)
- [ ] Camera integration (picamera2 + v4l2 autofocus)
- [ ] RinglightController + CameraController
- [ ] REST endpoints for both

### ✅ Phase 3 (Then)
- [ ] ArUco detection & positioning
- [ ] Scan path generation
- [ ] Real-time WebSocket preview

---

## Cleanup & Logs

**View live logs**:
```bash
tail -f logs/openscan_*.log
```

**Check server uptime**:
```bash
curl http://192.168.178.135:8000/api/v1/health
# {"status": "ok", "service": "openscan-mini"}
```

**Stop server**:
```bash
# Press Ctrl+C in the server terminal
```

---

**Status**: Motor control code complete & ready for hardware testing  
**Next**: Test on real Pi, then proceed to Phase 2.2 (Ringlight + Camera)
