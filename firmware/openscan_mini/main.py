"""
OpenScan Mini Firmware — Main Entry Point

FastAPI application for OpenScan Mini hardware control.
Handles hardware initialization, API routing, and WebSocket connections.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from openscan_mini.config import HardwareConfig, setup_logging
from openscan_mini.controllers.hardware.motors import DualMotorController
from openscan_mini.controllers.hardware.lights import RinglightController
from openscan_mini.controllers.hardware.camera import CameraController, CameraSettings
from openscan_mini.controllers.scan import ScanEngine
from openscan_mini.routers.v1.motors import router as motors_router, set_motor_controller
from openscan_mini.routers.v1.lights import router as lights_router, set_ringlight_controller
from openscan_mini.routers.v1.camera import router as camera_router, set_camera_controller
from openscan_mini.routers.v1.scan import router as scan_router, set_scan_engine


# Configure logging
logger = setup_logging(log_level="INFO", json_format=True)
app_logger = logging.getLogger(__name__)


# Global instances (loaded on startup)
HARDWARE_CONFIG: HardwareConfig | None = None
MOTOR_CONTROLLER: DualMotorController | None = None


def load_hardware_config() -> HardwareConfig:
    """
    Load hardware configuration from JSON file.

    Searches in multiple locations:
    1. ~/.openscan/hardware.json (user home)
    2. ./configs/hardware_greenshield.json (project root)
    3. /etc/openscan/hardware.json (system-wide)

    Returns:
        HardwareConfig instance

    Raises:
        FileNotFoundError: If no config file found
    """
    candidates = [
        Path.home() / ".openscan" / "hardware.json",
        Path(__file__).parent.parent.parent / "configs" / "hardware_greenshield.json",
        Path("/etc/openscan/hardware.json"),
    ]

    for config_path in candidates:
        if config_path.exists():
            app_logger.info(f"Found hardware config: {config_path}")
            return HardwareConfig.from_json_file(config_path)

    app_logger.error(f"No hardware config found. Searched: {candidates}")
    raise FileNotFoundError(
        f"Hardware configuration not found. "
        f"Create one at {candidates[0]} or {candidates[1]}"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Handles startup and shutdown events.
    """
    # Startup
    app_logger.info("OpenScan Mini firmware starting up...")

    global HARDWARE_CONFIG, MOTOR_CONTROLLER

    # Load hardware configuration
    try:
        HARDWARE_CONFIG = load_hardware_config()
        app_logger.info(HARDWARE_CONFIG.dump_summary())
    except FileNotFoundError as e:
        app_logger.error(f"Failed to load hardware config: {e}")
        sys.exit(1)

    app_logger.info("✓ Hardware configured")

    # Initialize motor controller
    try:
        MOTOR_CONTROLLER = DualMotorController(HARDWARE_CONFIG)
        set_motor_controller(MOTOR_CONTROLLER)
        app_logger.info("✓ Motor controller initialized")
    except Exception as e:
        app_logger.error(f"Failed to initialize motor controller: {e}")
        sys.exit(1)

    # Stock ringlight: GPIO17 = ringlight1, GPIO27 = ringlight2 (Greenshield, confirmed OS2 audit)
    # Both pins drive the onboard LED ring around the camera.
    try:
        ringlight = RinglightController(pins=[17, 27], name="ringlight")
        set_ringlight_controller(ringlight)
        app_logger.info("✓ Ringlight initialized — GPIO17 + GPIO27")
    except Exception as e:
        app_logger.warning(f"Ringlight init failed (non-fatal): {e}")

    # Camera: Arducam IMX519 (CSI ribbon cable) via rpicam-still
    camera = None
    try:
        cam_cfg = HARDWARE_CONFIG.camera.capture_settings
        cam_settings = CameraSettings(
            shutter_us=cam_cfg.get("shutter_speed_us", 50000),
            gain=float(cam_cfg.get("gain", 1.0)),
            jpeg_quality=cam_cfg.get("jpeg_quality", 95),
            saturation=float(cam_cfg.get("saturation", 1.0)),
            contrast=float(cam_cfg.get("contrast", 1.0)),
            autofocus=cam_cfg.get("autofocus_enabled", True),
        )
        camera = CameraController(settings=cam_settings)
        set_camera_controller(camera)
        app_logger.info("✓ Camera initialized — IMX519 CSI via rpicam-still")
    except Exception as e:
        app_logger.warning(f"Camera init failed (non-fatal): {e}")

    # Scan engine — wires motor + camera + ringlight together
    try:
        from openscan_mini.routers.v1.lights import RINGLIGHT
        if MOTOR_CONTROLLER and camera:
            scan_engine = ScanEngine(MOTOR_CONTROLLER, camera, ringlight=RINGLIGHT)
            set_scan_engine(scan_engine)
            app_logger.info("✓ Scan engine initialized (ringlight: %s)", "yes" if RINGLIGHT else "no")
        else:
            app_logger.warning("Scan engine skipped — motors or camera not ready")
    except Exception as e:
        app_logger.warning(f"Scan engine init failed (non-fatal): {e}")

    app_logger.info("✓ FastAPI application ready")
    app_logger.info(f"✓ Listening on http://0.0.0.0:8000")
    app_logger.info(f"✓ API docs available at http://0.0.0.0:8000/docs")

    yield

    # Shutdown
    app_logger.info("OpenScan Mini firmware shutting down...")

    # Cleanup
    if MOTOR_CONTROLLER:
        MOTOR_CONTROLLER.cleanup()
        app_logger.info("✓ Motor controller cleaned up")

    from openscan_mini.routers.v1.lights import RINGLIGHT
    if RINGLIGHT:
        RINGLIGHT.cleanup()

    app_logger.info("✓ Cleanup complete")


# Create FastAPI app
app = FastAPI(
    title="OpenScan Mini Firmware",
    description="High-Performance 3D Scanner Control API",
    version="0.2.0-dev",
    lifespan=lifespan,
)

# CORS middleware (allow all origins for local network)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(motors_router)
app.include_router(lights_router)
app.include_router(camera_router)
app.include_router(scan_router)

# Serve UI — static/index.html at /
_static_dir = Path(__file__).parent.parent.parent / "static"
if _static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="static")

@app.get("/")
async def root():
    """Redirect root to the UI."""
    ui = _static_dir / "index.html"
    if ui.exists():
        return FileResponse(str(ui))
    return JSONResponse({"message": "OpenScan Mini API", "docs": "/docs", "ui": "static/index.html not found"})


# ============================================================================
# HEALTH CHECK & STATUS ENDPOINTS
# ============================================================================


@app.get("/api/v1/status")
async def get_status():
    """
    Get device status and system information.

    Returns:
        Device info, hardware config summary, uptime, etc.
    """
    if not HARDWARE_CONFIG:
        return JSONResponse(
            status_code=500,
            content={"error": "Hardware not initialized"},
        )

    return {
        "device": {
            "name": HARDWARE_CONFIG.device.name,
            "model": HARDWARE_CONFIG.device.model,
            "shield": HARDWARE_CONFIG.device.shield,
            "pi_model": HARDWARE_CONFIG.device.pi_model,
            "os": HARDWARE_CONFIG.device.os,
        },
        "firmware": {
            "version": "0.2.0-dev",
            "api_version": "v1",
        },
        "status": "ready",
        "motors": {
            "rotor": {
                "position": 0.0,
                "min_angle": HARDWARE_CONFIG.motors["rotor"].min_angle,
                "max_angle": HARDWARE_CONFIG.motors["rotor"].max_angle,
            },
            "turntable": {
                "position": 0.0,
                "min_angle": HARDWARE_CONFIG.motors["turntable"].min_angle,
                "max_angle": HARDWARE_CONFIG.motors["turntable"].max_angle,
            },
        },
        "camera": {
            "type": HARDWARE_CONFIG.camera.type,
            "resolution": HARDWARE_CONFIG.camera.resolution,
            "autofocus": HARDWARE_CONFIG.camera.autofocus.get("enabled", False),
        },
        "ringlight": {
            "channels": len(HARDWARE_CONFIG.ringlight.channels),
            "power": HARDWARE_CONFIG.ringlight.power,
        },
    }


@app.get("/api/v1/health")
async def health_check():
    """Quick health check endpoint."""
    return {"status": "ok", "service": "openscan-mini"}


@app.get("/docs")
async def swagger_ui():
    """Redirect to API documentation."""
    return JSONResponse({"message": "API docs available at /docs"})


# ============================================================================
# PLACEHOLDER ROUTES (to be implemented in later phases)
# ============================================================================


@app.post("/api/v1/hardware/ringlight")
async def set_ringlight_brightness(brightness: int = 200):
    """
    Set ringlight brightness for all channels.

    Args:
        brightness: Brightness level (0-255)

    Returns:
        Confirmation
    """
    if brightness < 0 or brightness > 255:
        return JSONResponse(
            status_code=400,
            content={"error": "Brightness must be 0-255"},
        )

    app_logger.info(f"Ringlight brightness requested: {brightness}")

    # TODO: Implement PWM control in Phase 2
    return {"status": "ok", "brightness": brightness, "message": "Ringlight control not yet implemented"}


@app.websocket("/ws/scan")
async def websocket_scan(websocket: WebSocket):
    """
    WebSocket endpoint for real-time scan preview and status.

    Sends events:
    - scan_progress: Current scan step and preview image
    - motor_position: Current motor angles
    - error: Error messages
    """
    await websocket.accept()
    app_logger.info("WebSocket client connected: /ws/scan")

    try:
        while True:
            data = await websocket.receive_text()
            app_logger.debug(f"WebSocket received: {data}")

            # Echo back for now
            await websocket.send_json({"type": "echo", "message": data})

    except Exception as e:
        app_logger.error(f"WebSocket error: {e}")
    finally:
        app_logger.info("WebSocket client disconnected")


# ============================================================================
# ERROR HANDLERS
# ============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Handle uncaught exceptions."""
    app_logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


@app.exception_handler(404)
async def not_found_handler(request, exc):
    """Handle 404 not found."""
    return JSONResponse(
        status_code=404,
        content={"error": "Endpoint not found", "path": str(request.url.path)},
    )


# ============================================================================
# ENTRY POINT
# ============================================================================


def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """
    Run the FastAPI development server.

    Args:
        host: Host to bind to
        port: Port to bind to
        reload: Enable auto-reload on code changes
    """
    import uvicorn

    app_logger.info(f"Starting uvicorn server on {host}:{port}")

    uvicorn.run(
        "openscan_mini.main:app",
        host=host,
        port=port,
        reload=reload,
        log_config=None,  # Use our custom logging config
    )


if __name__ == "__main__":
    run_server(reload=True)  # Enable reload for development
