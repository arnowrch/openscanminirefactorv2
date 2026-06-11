# OpenScan Mini — Pi OS Setup & Installation Guide

**Target**: Raspberry Pi 4 with Raspberry Pi OS (Bookworm) 64-bit  
**Status**: Verified for June 2026 builds

---

## Phase 1: SD Card Preparation

### 1.1 Flashing with Raspberry Pi Imager

1. **Download & install** [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (v2.0.6+)
2. **Insert SD card** into your PC
3. **Open Raspberry Pi Imager**
4. **Select OS**:
   - Choose: `Other general-purpose OS` → `Raspberry Pi OS (other)` → **`Raspberry Pi OS (Lite, 64-bit)`**
   - Do **NOT** use Desktop version (uses more RAM/storage, less suitable for headless scanning)
5. **Select Storage**: Your SD card
6. **Open Advanced Options** (gear icon):
   ```
   ✓ Set hostname: openscan
   ✓ Enable SSH: YES (key-based auth)
   ✓ Set username & password: pi / openscan (or your choice)
   ✓ Configure WiFi: YES (or skip, use Ethernet)
   ✓ Set timezone: Your timezone
   ```
7. **Write**: Click "Next" → Confirm → Wait for completion

### 1.2 Post-Flash: Insert into Pi & First Boot

```bash
# Insert SD card into Pi 4
# Connect Ethernet cable (or WiFi configured)
# Connect 12V power supply

# Wait ~2 minutes for boot
# Find Pi on network:
arp -a                    # macOS/Linux
Get-NetNeighbor           # Windows PowerShell
# Or check your router's DHCP table for "openscan" hostname
```

**Access via SSH:**
```bash
ssh pi@openscan.local
# Password: openscan (or your password)
```

---

## Phase 2: System Updates & Dependencies

Run these commands on the Pi:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python dev tools & build essentials
sudo apt install -y \
  python3-dev \
  python3-pip \
  python3-venv \
  build-essential \
  libssl-dev \
  libffi-dev

# Install system libraries for OpenCV, camera, GPIO
sudo apt install -y \
  libatlas-base-dev \
  libjasper-dev \
  libtiff5 \
  libjasper1 \
  libharfbuzz0b \
  libwebp6 \
  libtiff5 \
  libjasper1 \
  libharfbuzz0b \
  libwebp6 \
  libopenjp2-7 \
  libtiff5 \
  libatlas-base-dev \
  libharfbuzz0b \
  libwebp6

# Install gphoto2 (for external camera support, optional)
sudo apt install -y gphoto2 libgphoto2-dev

# Install libcamera tools (for debugging)
sudo apt install -y libcamera-tools

# Install git
sudo apt install -y git

# Set GPU memory (for camera performance)
sudo raspi-config --non-interactive nonint do_gpu_mem 128
```

---

## Phase 3: IMX519 Arducam Setup

### 3.1 Enable Camera in raspi-config

```bash
sudo raspi-config

# Navigate to:
# 3 Interface Options → P1 Legacy Camera → <Yes>
# (For 64-bit Bookworm, also ensure libcamera is enabled)

# Reboot after changes
sudo reboot
```

### 3.2 Test Camera

```bash
# List available cameras
v4l2-ctl --list-devices

# Should show something like:
# Arducam 64MP IMX519 (usb-3f980000.usb-1.1):
#     /dev/video0
#     /dev/media0

# Test capture (quick preview)
libcamera-hello --preview=none --timeout=5000 -o test.jpg
ls -lh test.jpg     # Should be ~2-5 MB
```

### 3.3 v4l2 Control Setup (for Autofocus)

```bash
# Install v4l2-ctl
sudo apt install -y v4l2-utils

# Test focus control
v4l2-ctl -d /dev/video0 --list-ctrls | grep -i focus

# Should show something like:
#     focus_absolute (0x009a090a)
#     focus_absolute (0x009a090a): min=0 max=1000 step=1 default=500 value=500

# Test focus adjustment
v4l2-ctl -d /dev/video0 -c focus_absolute=200  # Closer
v4l2-ctl -d /dev/video0 -c focus_absolute=800  # Further
```

---

## Phase 4: GPIO & Hardware Setup

### 4.1 Install gpiozero

```bash
# Create Python virtual environment (recommended)
python3 -m venv ~/openscan_env
source ~/openscan_env/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install gpiozero with ALL extras (for PWM, etc.)
pip install gpiozero[all]

# Install other core dependencies
pip install numpy opencv-python
```

### 4.2 Test GPIO (Basic)

```python
# Test file: ~/test_gpio.py
from gpiozero import MotionSensor, LED
import time

# Test LED on GPIO 26 (Ringlight channel 1)
led = LED(26)
led.on()
print("LED ON")
time.sleep(2)
led.off()
print("LED OFF")
led.close()
```

Run:
```bash
python3 ~/test_gpio.py
```

If LED flickers → GPIO is working!

---

## Phase 5: OpenScan Firmware Installation

### 5.1 Clone/Copy Your Firmware Repository

```bash
cd ~
git clone <your-repo-url> openscan-firmware
cd openscan-firmware

# Or copy via SCP from your development machine:
# scp -r ./firmware pi@openscan.local:~/openscan-firmware
```

### 5.2 Install Firmware Dependencies

```bash
cd ~/openscan-firmware

# Activate virtual environment
source ~/openscan_env/bin/activate

# Install FastAPI and dependencies
pip install -r requirements.txt

# Key packages:
pip install fastapi uvicorn aiofiles websockets
```

### 5.3 Create Configuration Directory

```bash
mkdir -p ~/.openscan/settings
cp ./configs/hardware_greenshield.json ~/.openscan/hardware.json
```

---

## Phase 6: Configure Boot & Systemd Service

### 6.1 Create Systemd Service

Create file: `/etc/systemd/system/openscan.service`

```ini
[Unit]
Description=OpenScan Mini Firmware
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/openscan-firmware
Environment="PATH=/home/pi/openscan_env/bin"
ExecStart=/home/pi/openscan_env/bin/python3 -m openscan_mini.main
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable & start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable openscan.service
sudo systemctl start openscan.service

# Check status
sudo systemctl status openscan.service

# View logs (live)
sudo journalctl -u openscan.service -f
```

---

## Phase 7: Network Configuration

### 7.1 Static IP (recommended for reliability)

Edit: `/etc/dhcpcd.conf`

```bash
sudo nano /etc/dhcpcd.conf
```

Add at the end:

```
interface eth0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1 8.8.8.8
```

Or via raspi-config:
```bash
sudo raspi-config → 6 Advanced Options → A4 Hostname
```

### 7.2 Access via Hostname

```bash
# From development machine
ssh pi@openscan.local
curl http://openscan.local:8000/api/v1/status
```

---

## Phase 8: Configure Boot Parameters (config.txt)

Edit: `/boot/firmware/config.txt`

```bash
sudo nano /boot/firmware/config.txt
```

Key settings for OpenScan:

```ini
# GPU Memory (for camera performance)
gpu_mem=128

# Camera auto-detection (disable if manual config)
camera_auto_detect=0

# IMX519 support (Arducam)
dtoverlay=imx519

# Performance tweaks
arm_boost=1

# Disable overscan (for fullscreen UI if needed)
disable_overscan=1

# HDMI settings (if HDMI monitor used for debugging)
hdmi_blanking=2
```

Reboot:
```bash
sudo reboot
```

---

## Phase 9: Verification Checklist

Run on the Pi:

```bash
# 1. Camera test
libcamera-hello --preview=none --timeout=2000 -o /tmp/test.jpg
echo "✓ Camera works" || echo "✗ Camera FAILED"

# 2. GPIO test
python3 -c "from gpiozero import LED; l = LED(26); l.on(); print('✓ GPIO works'); l.off()"

# 3. Python environment
python3 -c "import fastapi, opencv, numpy; print('✓ Python deps OK')"

# 4. Firmware startup
# (systemd will auto-start)
sudo systemctl status openscan.service

# 5. API endpoint
curl http://localhost:8000/api/v1/status
# Should return JSON with device info
```

---

## Phase 10: Heimnetz-Integration (Optional)

### 10.1 Samba Share (for project folders)

```bash
sudo apt install -y samba samba-usb-support

# Create share directory
mkdir -p ~/OpenScan/projects
chmod 755 ~/OpenScan/projects

# Edit samba config
sudo nano /etc/samba/smb.conf
```

Add:

```ini
[OpenScan]
    path = /home/pi/OpenScan/projects
    read only = no
    guest ok = yes
    create mask = 0644
    directory mask = 0755
```

Restart:
```bash
sudo systemctl restart smbd
```

Access from PC:
```bash
# Windows: \\openscan\OpenScan
# Linux: smb://openscan/OpenScan
# macOS: cmd+K → smb://openscan/OpenScan
```

### 10.2 Prometheus Metrics (optional monitoring)

```bash
pip install prometheus-client

# Add to your FastAPI app:
# from prometheus_client import Counter, generate_latest
# scan_count = Counter('openscan_scans_total', 'Total scans completed')
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **SSH connection refused** | Wait 2 min after boot, check IP with `arp -a` |
| **Camera not detected** | Run `libcamera-hello --list-cameras`, check USB connection |
| **GPIO Permission denied** | Ensure `pi` user is in `gpio` group: `sudo usermod -a -G gpio pi` |
| **Out of disk space** | `df -h` → if full, delete old scans or expand partition |
| **systemd service fails** | Check logs: `sudo journalctl -u openscan.service -n 50` |
| **Low FPS on preview** | Reduce resolution, increase JPEG quality threshold |

---

## Next Steps

1. ✓ Flash OS & verify Pi boots
2. ✓ Install dependencies & verify camera/GPIO
3. → **Clone your firmware repo & test API endpoints**
4. → **Test motor movement** with manual commands
5. → **Integrate ArUco detection** & position feedback
6. → **Build Web-UI** & real-time preview
7. → **Deploy to Heimnetz** processing pipeline

---

**Status**: Ready for firmware installation  
**Last updated**: June 11, 2026
