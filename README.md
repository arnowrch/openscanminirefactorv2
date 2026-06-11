# OpenScan Mini — High-Performance Firmware Refactor

> Professional-grade 3D scanner firmware for Raspberry Pi 4 (64-bit) with ArUco homing, real-time preview, and networked photogrammetry.

**Status**: Phase 2 (Core Firmware) — In Development

## Quick Start

### Prerequisites
- Raspberry Pi 4 (8GB RAM recommended)
- Raspberry Pi OS (Bookworm) 64-bit
- Arducam IMX519 (16MP with autofocus)
- Greenshield PCB

### Installation

1. **Prepare Pi OS** (see [SETUP.md](SETUP.md) for detailed instructions):
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y python3-venv build-essential libssl-dev
   ```

2. **Clone Repository & Install Firmware**:
   ```bash
   cd ~
   git clone <your-repo-url> openscan-firmware
   cd openscan-firmware/firmware

   # Create virtual environment
   python3 -m venv ~/openscan_env
   source ~/openscan_env/bin/activate

   # Install dependencies
   pip install -r requirements.txt
   ```

3. **Copy Hardware Configuration**:
   ```bash
   mkdir -p ~/.openscan
   cp ../configs/hardware_greenshield.json ~/.openscan/hardware.json
   ```

4. **Start Firmware Server**:
   ```bash
   python3 -m openscan_mini.main
   ```

   Server will start on `http://localhost:8000`

5. **Verify Installation**:
   ```bash
   # In another terminal
   curl http://localhost:8000/api/v1/status
   ```

## Project Structure

```
.
├── ARCHITECTURE.md              # System design & technical docs
├── SETUP.md                     # Pi OS installation guide
├── README.md                    # This file
├── firmware/
│   ├── openscan_mini/           # Main Python package
│   │   ├── main.py              # FastAPI entry point
│   │   ├── config/              # Configuration loading
│   │   ├── controllers/         # Hardware control logic
│   │   ├── models/              # Pydantic data models
│   │   ├── routers/             # REST API endpoints
│   │   └── services/            # Business logic
│   ├── pyproject.toml           # Package definition
│   ├── requirements.txt         # Python dependencies
│   └── README.md
├── configs/
│   ├── hardware_greenshield.json # Hardware pin configuration
│   └── pi_os_setup.sh           # Automated Pi setup script
├── tests/                       # Unit & integration tests
├── docs/                        # Additional documentation
├── sdk/                         # Heimnetz processing SDK
└── ui/                          # Web frontend (React)
```

## API Documentation

### Health Check
```bash
GET /api/v1/status
GET /api/v1/health
```

### Motor Control (Phase 2)
```bash
# Move rotor to 45°
POST /api/v1/hardware/motors/rotor -d '{"angle": 45.0}'

# Move turntable to 180°
POST /api/v1/hardware/motors/turntable -d '{"angle": 180.0}'

# Home to endstop
POST /api/v1/hardware/home
```

### Ringlight Control (Phase 2)
```bash
# Set brightness (0-255)
POST /api/v1/hardware/ringlight -d '{"brightness": 200}'
```

### Camera Control (Phase 2)
```bash
# Get preview image
GET /api/v1/cameras/preview

# Capture full-resolution image
POST /api/v1/cameras/capture

# Auto-focus
POST /api/v1/cameras/autofocus
```

### Scanning (Phase 3)
```bash
# Start scan
POST /api/v1/scans/start

# Get scan status
GET /api/v1/scans/status

# Live preview (WebSocket)
ws://localhost:8000/ws/scan
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for complete API specification.

## Development

### Running Tests
```bash
cd firmware
pip install -r requirements-dev.txt
pytest -v --cov=openscan_mini
```

### Code Style
```bash
# Format code
black openscan_mini/

# Lint
ruff check openscan_mini/

# Type check
mypy openscan_mini/
```

### IDE Setup
- **VS Code**: Install Python extension, select `openscan_env` interpreter
- **PyCharm**: File → Settings → Project → Python Interpreter → Add → Existing environment

## Phases & Roadmap

### ✅ Phase 1: Architecture & Setup (Complete)
- Hardware documentation & GPIO pin mapping
- Pi OS setup guide for 64-bit Bookworm
- FastAPI scaffold with config loading
- Documentation (ARCHITECTURE.md, SETUP.md)

### 🔄 Phase 2: Core Firmware (Current)
- Motor control via gpiozero
- Ringlight PWM control
- Camera integration (picamera2 + autofocus)
- REST API endpoints + tests
- **ETA**: June 20, 2026

### 📅 Phase 3: Scanning Engine (Planned)
- ArUco detection & positioning
- Scan path generation (Fibonacci, Grid, Spiral)
- Real-time WebSocket preview
- Scan orchestration & progress tracking
- **ETA**: July 5, 2026

### 📅 Phase 4: Web-UI (Planned)
- React-based frontend
- Live camera preview
- Motor jogging interface
- Scan control panel
- Project browser
- **ETA**: July 20, 2026

### 📅 Phase 5: Heimnetz Integration (Planned)
- Network image transfer (SMB/HTTP)
- COLMAP orchestration
- Meshroom pipeline wrapper
- 3D mesh generation & retrieval
- **ETA**: August 10, 2026

## Hardware Setup

### GPIO Pin Configuration
See [configs/hardware_greenshield.json](configs/hardware_greenshield.json) for:
- Motor pins (rotor, turntable)
- Endstop switches
- Ringlight PWM channels
- Camera interface

### Ringlight Circuit (12V, 2000mA)
```
GPIO 26 (or 19) → [MOSFET Gate] → 12V Supply → Ringlight LED
                   [Drain]
                      ↓
                  [12V GND]

Components needed:
- 1× MOSFET (2N7000 or IRF520N)
- 1× 1kΩ resistor (gate protection)
- 1× 1N4007 diode (back-EMF protection)
```

See [SETUP.md](SETUP.md) § 4.2 for detailed wiring instructions.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: picamera2` | Only works on actual Pi hardware. On dev machine, run without picamera2 dependency. |
| `PermissionError: /dev/gpiochip0` | Add `pi` user to `gpio` group: `sudo usermod -a -G gpio pi` |
| `FileNotFoundError: hardware.json` | Copy `configs/hardware_greenshield.json` to `~/.openscan/hardware.json` |
| Server won't start | Check logs: `tail -f logs/openscan_*.log` |

## License

GPL-3.0 — See [LICENSE](LICENSE) file

## Contributing

See [ARCHITECTURE.md](ARCHITECTURE.md) § 8 for code standards and best practices.

## Support

- **Discord**: OpenScan community (linked in main repo)
- **Issues**: GitHub issues
- **Email**: dev@openscan-mini.local

---

**Last Updated**: June 11, 2026  
**Maintainer**: OpenScan Mini Refactor  
**Version**: 0.2.0-dev
