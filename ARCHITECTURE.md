# OpenScan Mini Refactor — System Architecture

**Version**: 1.0-dev  
**Status**: Konzept & Implementierung  
**Hardware**: Raspberry Pi 4 (64-bit Bookworm), Greenshield, Arducam IMX519  
**Target Performance**: High-precision 3D scanning with real-time feedback & networked photogrammetry

---

## 1. Systemübersicht

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OPENSCAN MINI (RASPBERRY PI 4)                   │
│                      64-bit Bookworm, Python 3.11+                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              FastAPI Application (Port 8000)                 │  │
│  ├──────────────────────────────────────────────────────────────┤  │
│  │                                                              │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │  │
│  │  │   Hardware  │  │    Scanning  │  │   Processing &   │   │  │
│  │  │  Controllers│  │    Engine    │  │   State Manager  │   │  │
│  │  ├─────────────┤  ├──────────────┤  ├──────────────────┤   │  │
│  │  │• Motors     │  │• Scan paths  │  │• Project mgmt    │   │  │
│  │  │• Ringlight  │  │• ArUco Home  │  │• Cloud sync      │   │  │
│  │  │• Endstops   │  │• Real-time   │  │• Metadata store  │   │  │
│  │  │• Camera     │  │  feedback    │  │• Event emitter   │   │  │
│  │  └─────────────┘  └──────────────┘  └──────────────────┘   │  │
│  │         │                 │                  │                │  │
│  │  ┌──────┴──────────────────┴──────────────────┴────────┐    │  │
│  │  │     WebSocket & REST API Routers (v1, v2-next)     │    │  │
│  │  │  • Live preview stream                             │    │  │
│  │  │  • Motor position / status                         │    │  │
│  │  │  • Scan control & settings                         │    │  │
│  │  │  • ArUco position feedback                         │    │  │
│  │  │  • Network-mounted projects                        │    │  │
│  │  └──────┬───────────────────────────────────────────────┘   │  │
│  │         │                                                    │  │
│  └─────────┼────────────────────────────────────────────────────┘  │
│            │                                                        │
│  GPIO/I2C/USB Hardware Interfaces                                  │
│    • Stepper motors (via gpiozero)                                │
│    • Endstop switches (GPIO)                                      │
│    • 12V Ringlight PWM control                                    │
│    • Arducam IMX519 (USB → libcamera/picamera2)                  │
│                                                                    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                    Network (Ethernet/WiFi)
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
   ┌─────────────┐   ┌──────────────┐   ┌────────────────┐
   │  Web UI     │   │ Heim-PC      │   │  Cloud Sync    │
   │ (React/Vue) │   │ (AMD RX 470) │   │ (OpenScanCloud)│
   │ http://     │   │              │   │                │
   │ openscan:80 │   │ Meshroom /   │   │ Project backup │
   │             │   │ COLMAP /     │   │ & sharing      │
   │ Real-time   │   │ Reality Capt.│   │                │
   │ preview &   │   │              │   │                │
   │ control     │   │ GPU-accel    │   │                │
   │             │   │ 3D mesh gen  │   │                │
   └─────────────┘   └──────────────┘   └────────────────┘
```

---

## 2. Hardware-Konfiguration: Greenshield + IMX519

### 2.1 GPIO-Belegung (Raspberry Pi 4)

```yaml
MOTORS:
  ROTOR:
    step_pin: 27          # GPIO 27
    dir_pin: 17           # GPIO 17
    enable_pin: 22        # GPIO 22
    steps_per_rotation: 10240    # Microstepping
    max_speed: 5000 steps/sec
    acceleration: 10000 steps/sec²
    angle_range: 0–145°
    
  TURNTABLE:
    step_pin: 16          # GPIO 16
    dir_pin: 6            # GPIO 6
    enable_pin: 22        # GPIO 22 (shared with rotor)
    steps_per_rotation: 800
    max_speed: 3000 steps/sec
    acceleration: 8000 steps/sec²
    angle_range: 0–360°

ENDSTOPS:
  ROTOR:
    pin: 18               # GPIO 18 (endstop switch)
    position: 145°        # Physical limit
    pull_up: true
    debounce_ms: 50

RINGLIGHT:
  PIN_1: 26              # GPIO 26 (PWM capable)
  PIN_2: 19              # GPIO 19 (PWM capable)
  TYPE: 12V, 2000mA
  CIRCUIT: GPIO → MOSFET/Transistor → 12V supply
  
CAMERA:
  TYPE: Arducam IMX519 (16MP)
  INTERFACE: USB 3.0
  FOCUS: v4l2 software control (0-1000 range)
  AUTOFOCUS: Supported via v4l2-ctl or picamera2
```

### 2.2 Ringlight-Anschluss (Hardware)

**Schema für 12V Ringlight mit GPIO-Steuerung:**

```
Raspberry Pi GPIO
      │
      ├─ GPIO 26 ──→ [MOSFET Gate] ──→ GND
      │                  │
      │              Drain (12V side)
      │                  │
      └─ 12V Power ──→ [Ringlight +12V]
                          │
                       [Ringlight GND] ──→ Power Supply GND

Alternative mit Transistor (wenn MOSFET nicht vorhanden):
      GPIO 26 ──→ [1k Resistor] ──→ [BC547 Base]
                                       │
                              12V ──→ [Collector]
                                       │
                                   [Ringlight]
                                       │
                                       └─→ GND
```

**Hardware-komponenten:**
- 1× MOSFET (z.B. 2N7000, IRF520N) oder NPN-Transistor (BC547)
- 1× 1kΩ Vorwiderstand (für Transistor)
- 1× Schutzdiode (1N4007) parallel zur Ringlight (gegen Back-EMF)
- Verbindung: JST XH connector (wie original)

---

## 3. Firmware-Stack

### 3.1 Technology Stack

| Layer | Technology | Version | Notes |
|-------|-----------|---------|-------|
| **OS** | Raspberry Pi OS (Bookworm) | 64-bit | Full hardware support |
| **Runtime** | Python | 3.11+ | Modern, typed, async-ready |
| **Framework** | FastAPI | 0.104+ | Async, WebSocket, auto-docs |
| **Camera** | picamera2 + gphoto2 | Latest | 64-bit native, autofocus via v4l2 |
| **GPIO** | gpiozero | 2.0+ | Modern, non-blocking |
| **Computer Vision** | OpenCV | 4.8+ | ArUco detection, image processing |
| **Task Queue** | APScheduler | 3.10+ | Background jobs, scan routines |
| **WebSocket** | FastAPI websockets | Built-in | Real-time preview & status |
| **Database** | SQLite / JSON | File-based | Project metadata, scan history |
| **Testing** | pytest + pytest-asyncio | Latest | Full async test support |

### 3.2 Modul-Struktur

```
firmware/openscan_mini/
├── main.py                          # FastAPI entry point
├── config/
│   ├── __init__.py
│   ├── hardware.py                  # Greenshield-Konfiguration
│   ├── camera.py                    # IMX519-Einstellungen
│   ├── scan.py                      # Scanning-Parameter
│   └── logger.py                    # Strukturiertes Logging
│
├── controllers/
│   ├── hardware/
│   │   ├── motors.py                # gpiozero stepper control
│   │   ├── endstops.py              # Endstop detection
│   │   ├── camera.py                # picamera2 + v4l2-ctl
│   │   ├── ringlight.py             # PWM LED control
│   │   └── gpio.py                  # Raw GPIO operations
│   ├── scanning/
│   │   ├── engine.py                # Main scan orchestrator
│   │   ├── paths.py                 # Fibonacci, Grid, Spiral
│   │   └── homing.py                # ArUco-basiertes Homing
│   └── processing/
│       ├── aruco.py                 # ArUco detection & tracking
│       └── pointcloud.py            # Lokale Wolken-Verarbeitung
│
├── routers/
│   ├── v1/
│   │   ├── hardware.py              # Motor, light, camera control
│   │   ├── scans.py                 # Scan start/stop/status
│   │   ├── projects.py              # Project CRUD
│   │   └── settings.py              # Config endpoints
│   └── v2_next/                     # Future API version
│
├── services/
│   ├── scan_service.py              # Scanning orchestration
│   ├── project_service.py           # Project management
│   ├── cloud_service.py             # OpenScanCloud integration
│   └── event_emitter.py             # WebSocket event broadcasting
│
├── models/
│   ├── scan.py                      # ScanRequest, ScanStatus
│   ├── project.py                   # ProjectMetadata
│   ├── position.py                  # MotorPosition, HomingData
│   └── camera.py                    # CameraSettings
│
└── __init__.py
```

---

## 4. Scanning-Workflow

### 4.1 Idealablauf (High-Performance)

```
1. INITIALIZATION
   ├─ Load hardware config (Greenshield pins)
   ├─ Initialize motors & endstops
   ├─ Home to ArUco marker (if present)
   └─ Initialize camera & autofocus

2. PRE-SCAN
   ├─ Detect ArUco position
   ├─ Calculate object bounds (via ArUco)
   ├─ Auto-adjust ringlight intensity
   ├─ Set camera exposure & focus
   └─ Confirm ready state via WebSocket

3. SCANNING
   ├─ Generate scan path (Fibonacci spiral)
   ├─ For each position:
   │  ├─ Move rotor & turntable
   │  ├─ Detect current position via ArUco (optional)
   │  ├─ Capture image (IMX519 autofocus)
   │  ├─ Stream preview to UI (WebSocket)
   │  └─ Store raw image & metadata
   └─ Report progress (%)

4. POST-SCAN
   ├─ Archive images locally
   ├─ Generate project metadata
   ├─ Optionally compress & upload to cloud
   └─ Trigger network-local processing

5. PROCESSING (on Heim-PC/Jetson)
   ├─ Receive image batch (SMB/HTTP)
   ├─ Run Meshroom / COLMAP / Reality Capture
   ├─ Generate 3D mesh & pointcloud
   ├─ Return results to Pi (optional)
   └─ Notify user via WebSocket
```

### 4.2 ArUco-basiertes Homing & Positioning

**Ziel:** Genaue Position der 180°-Achse (Kamera-Arm) bestimmen

```python
1. Marker-Platzierung:
   ┌──────────────────────┐
   │                      │
   │  ┌────────────────┐  │
   │  │  Object        │  │
   │  │  mit ArUco     │  │
   │  │  Marker        │  │
   │  └────────────────┘  │
   │       ↑               │
   │    (0°,0°)          │
   │                      │
   └──────────────────────┘

2. Kamera-Arm rotiert um 180° Achse
   → Marker bleibt im Bild
   → Position relativ zu Marker bestimmt Motor-Winkel

3. Turntable rotiert 360°
   → Marker dreht mit
   → Turntable-Position bestimmt

Ergebnis: 
  - Rotor_angle = atan2(marker_Y - center_Y, marker_X - center_X)
  - Turntable_angle = marker_rotation_angle
```

---

## 5. Heimnetz-Integration (Processing Pipeline)

### 5.1 Workflow: Pi → Heimnetz → 3D Mesh

```
Pi (OpenScan Mini)
├─ Scan complete
├─ Save images locally
└─ Send event: "scan_ready"
     │
     ▼
Heim-PC (AMD RX 470 / Jetson Nano)
├─ Monitor SMB share or REST endpoint
├─ Detect new scan folder
├─ Download images (HTTP/SMB)
├─ Run photogrammetry
│  ├─ COLMAP (CPU + GPU acceleration)
│  ├─ Meshroom (GUI-based, automated)
│  └─ Reality Capture (commercial, fastest)
├─ Generate:
│  ├─ Sparse pointcloud
│  ├─ Dense pointcloud
│  └─ Textured mesh (.obj / .ply)
└─ Return results
     │
     ▼
Pi (Optional: visualize, store metadata)
└─ Store mesh in project folder
   Archive & cleanup
```

### 5.2 GPU-Acceleration Options

| Hardware | Framework | Notes |
|----------|-----------|-------|
| **AMD RX 470** | OpenCL / HIP | COLMAP + ROCm, Meshroom GPU backend |
| **Jetson Nano** | CUDA | COLMAP + CUDA, TensorRT for inference |
| **Local PC** | OpenGL + CPU | Fallback: slower but standalone |

---

## 6. Web-UI & API

### 6.1 REST API (v1)

```
GET    /api/v1/status              → Device status, temperatures
POST   /api/v1/scan/start          → Begin scan
GET    /api/v1/scan/status         → Current scan progress
POST   /api/v1/scan/stop           → Abort scan
GET    /api/v1/projects            → List projects
POST   /api/v1/projects            → Create new project
GET    /api/v1/hardware/motors     → Motor positions
POST   /api/v1/hardware/motors     → Move motor
GET    /api/v1/hardware/ringlight  → Light status
POST   /api/v1/hardware/ringlight  → Set brightness
POST   /api/v1/hardware/home       → Execute homing routine
```

### 6.2 WebSocket Events

```
ws://openscan:8000/ws/scan

SUBSCRIBE TO:
- scan_progress: { "step": 25, "total": 100, "image": "base64_preview" }
- motor_position: { "rotor": 45.2, "turntable": 180.5 }
- aruco_detected: { "position": [x, y], "angle": 12.5 }
- ringlight_status: { "brightness": 80 }
- error: { "message": "Endstop hit!" }
```

---

## 7. Development Roadmap

### Phase 1: Core Firmware (Week 1-2)
- [ ] FastAPI scaffold mit Greenshield-Config
- [ ] Motor control (gpiozero stepper)
- [ ] Camera integration (picamera2 + IMX519 autofocus)
- [ ] Ringlight PWM control
- [ ] Endstop detection & homing
- [ ] Basic REST API

### Phase 2: ArUco & Scanning (Week 3-4)
- [ ] ArUco marker detection & tracking
- [ ] Position calculation from ArUco
- [ ] Scan path generation (Fibonacci)
- [ ] Real-time preview via WebSocket
- [ ] Scan orchestration engine

### Phase 3: Web-UI & Integration (Week 5-6)
- [ ] Modern reactive UI (React/Vue)
- [ ] Live camera preview
- [ ] Motor jogging & homing
- [ ] Scan control panel
- [ ] Project browser

### Phase 4: Heimnetz-Processing (Week 7-8)
- [ ] Network discovery (mDNS)
- [ ] Image transfer to Heim-PC
- [ ] Photogrammetry orchestration
- [ ] Result retrieval & visualization

### Phase 5: Polish & Optimization (Week 9+)
- [ ] Performance tuning (GPU acceleration)
- [ ] Error handling & recovery
- [ ] Comprehensive testing
- [ ] Documentation & user guides

---

## 8. Coding Standards & Best Practices

### Style
- **Python**: PEP 8, type hints, async/await
- **Git**: Conventional commits (`feat:`, `fix:`, `docs:`)
- **Testing**: pytest, >80% coverage goal

### Documentation
- Each module: docstring with purpose, dependencies, examples
- Each API endpoint: OpenAPI schema (auto-generated by FastAPI)
- Hardware setup: Fritzing diagrams + photos

### Performance Targets
- **Scan startup**: <5s from API call to first motor move
- **Image capture**: 1.5–2s per position (IMX519 + autofocus)
- **Preview FPS**: 15+ FPS (WebSocket stream, downscaled)
- **Mesh generation**: <30min for high-detail scan (on AMD RX 470)

---

## 9. Next Steps

1. **Set up Pi OS 64-bit** with dependencies (see SETUP.md)
2. **Verify hardware connections** (motors, ringlight, endstops)
3. **Build firmware skeleton** (FastAPI app + GPIO tests)
4. **Test motor movement** with manual API calls
5. **Integrate camera** and verify autofocus
6. **Implement ArUco detection** and position calculation
7. **Build scanning engine** and real-time preview
8. **Develop Web-UI**
9. **Integration test** with Heimnetz processing

---

**Status:** Ready for Phase 1 implementation  
**Contact:** This is your personal project repo — extend as needed!
