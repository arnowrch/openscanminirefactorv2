"""REST API endpoints for Arducam IMX519 camera control."""

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from openscan_mini.controllers.hardware.analysis import FrameAnalysis, analyze

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hardware/camera", tags=["camera"])

CAMERA = None

# ── Shared analysis state (updated by stream loop) ──────────────────────────
_analysis: FrameAnalysis = FrameAnalysis()
_analysis_ts: float = 0.0
_auto_adjust_enabled: bool = True   # apply suggestions automatically
_stream_clients: int = 0            # how many browsers are watching


def set_camera_controller(controller) -> None:
    global CAMERA
    CAMERA = controller


def _get():
    if not CAMERA:
        raise HTTPException(status_code=503, detail="Camera not ready — server still starting up")
    return CAMERA


# ── Request models ────────────────────────────────────────────────────────────

class FocusRequest(BaseModel):
    lens_position: float = Field(
        ..., ge=0.0, le=32.0,
        description="Diopters: 0.0=infinity, 2.0=50cm, 10.0=10cm"
    )

class ExposureRequest(BaseModel):
    shutter_us: int = Field(..., ge=100, le=1_000_000)

class AutofocusRequest(BaseModel):
    enabled: bool

class AutoAdjustRequest(BaseModel):
    enabled: bool = Field(..., description="Enable/disable auto exposure/gain from analysis")


# ── MJPEG Stream ─────────────────────────────────────────────────────────────

@router.get("/stream")
async def mjpeg_stream(request: Request):
    """
    MJPEG live stream (~12fps idle, pauses during captures).
    Runs analysis every 5th frame and stores result for /analysis endpoint.
    """
    cam = _get()
    global _stream_clients
    _stream_clients += 1

    async def frame_generator():
        global _analysis, _analysis_ts, _stream_clients
        loop = asyncio.get_event_loop()
        frame_n = 0
        try:
            while True:
                if await request.is_disconnected():
                    break

                # Grab frame in thread pool (picamera2 blocks)
                jpeg, rgb = await loop.run_in_executor(None, cam.grab_stream_frame)

                if jpeg:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + jpeg +
                        b"\r\n"
                    )

                # Analysis every 5th frame
                frame_n += 1
                if frame_n % 5 == 0 and rgb is not None:
                    try:
                        result = await loop.run_in_executor(
                            None,
                            analyze,
                            rgb,
                            cam.settings.shutter_us,
                            cam.settings.gain,
                        )
                        _analysis = result
                        _analysis_ts = time.time()

                        # Auto-apply suggestions — skip during AF/capture (busy)
                        if _auto_adjust_enabled and not cam._busy:
                            if result.suggested_shutter_us or result.suggested_gain:
                                await loop.run_in_executor(
                                    None,
                                    cam.apply_auto_adjust,
                                    result.suggested_shutter_us,
                                    result.suggested_gain,
                                )
                    except Exception as e:
                        logger.debug(f"Analysis error: {e}")

                await asyncio.sleep(0.083)  # ~12fps

        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            _stream_clients -= 1

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Analysis state ────────────────────────────────────────────────────────────

@router.get("/analysis")
async def get_analysis():
    """Current frame analysis: sharpness, brightness, crop suggestion."""
    a = _analysis
    age = round(time.time() - _analysis_ts, 1) if _analysis_ts else None
    crop = list(a.crop_box) if a.crop_box else None
    return {
        "sharpness": round(a.sharpness, 1),
        "brightness": round(a.brightness, 1),
        "is_sharp": a.is_sharp,
        "is_exposed": a.is_exposed,
        "crop_box": crop,
        "suggested_shutter_us": a.suggested_shutter_us,
        "suggested_gain": a.suggested_gain,
        "auto_adjust": _auto_adjust_enabled,
        "stream_clients": _stream_clients,
        "analysis_age_s": age,
    }


@router.post("/analysis/auto-adjust")
async def set_auto_adjust(request: AutoAdjustRequest):
    """Enable or disable automatic exposure/gain adjustment from analysis."""
    global _auto_adjust_enabled
    _auto_adjust_enabled = request.enabled
    return {"auto_adjust": _auto_adjust_enabled, "status": "ok"}


# ── Standard endpoints ────────────────────────────────────────────────────────

@router.get("/")
async def get_camera_status() -> dict:
    return _get().get_status()


@router.get("/preview")
async def preview_jpeg():
    """Single JPEG preview (960×720). Kept for fallback/snapshot use."""
    cam = _get()
    if cam._busy:
        raise HTTPException(status_code=503, detail="Camera busy")
    try:
        data = cam.capture_preview()
        return Response(content=data, media_type="image/jpeg")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/capture")
async def capture_jpeg():
    """Full-resolution capture (4656×3496)."""
    cam = _get()
    if cam._busy:
        raise HTTPException(status_code=503, detail="Camera busy")
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, cam.capture_jpeg)
        return Response(content=data, media_type="image/jpeg")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/focus")
async def set_focus(request: FocusRequest) -> dict:
    _get().set_focus(request.lens_position)
    return {"lens_position": request.lens_position, "autofocus": False, "status": "ok"}


@router.post("/autofocus")
async def set_autofocus(request: AutofocusRequest) -> dict:
    _get().set_autofocus(request.enabled)
    return {"autofocus": request.enabled, "status": "ok"}


def _af_trigger_sync(cam):
    """
    Manual AF: full in-process VCM sharpness sweep (fast, no camera handoff).
    rpicam-still --autofocus remains available as a fallback via _do_autofocus_rpicam.
    _busy is already set by the caller; call the internal method directly.
    """
    best_pos = cam._do_autofocus_sweep()
    return True, None, best_pos


@router.post("/autofocus/trigger")
async def trigger_autofocus():
    """
    Run a single one-shot AF cycle (matches OS2 libcamera-still --autofocus behavior).
    Works on all picamera2 versions — falls back to manual trigger if autofocus_cycle() absent.
    """
    cam = _get()
    if not _PICAM2_AVAILABLE:
        return {"status": "ok", "focused": None, "note": "rpicam fallback, no trigger available"}

    loop = asyncio.get_event_loop()

    async def _run():
        if cam._busy:
            return {"status": "busy"}
        with cam._lock:
            cam._busy = True
        try:
            success, af_state, lens_pos = await loop.run_in_executor(
                None, lambda: _af_trigger_sync(cam)
            )
            cam.settings.autofocus = True
            # Stay in AfMode.Auto — lens holds the focused position.
            # Do NOT call _apply_continuous_af(): restarts scanning and drifts focus.
            return {
                "status": "ok",
                "focused": success,
                "af_state": af_state,
                "lens_position": lens_pos,
            }
        finally:
            with cam._lock:
                cam._busy = False

    return await _run()


@router.get("/debug")
async def camera_debug():
    """Diagnostic endpoint: AF state, lens position, picamera2 version, camera properties."""
    cam = _get()
    info: dict = {"picam2_available": _PICAM2_AVAILABLE}
    if not _PICAM2_AVAILABLE:
        return info

    try:
        import picamera2 as _pc2_mod
        info["picamera2_version"] = getattr(_pc2_mod, "__version__", "unknown")
        info["has_autofocus_cycle"] = hasattr(cam._picam, "autofocus_cycle")
    except Exception:
        pass

    try:
        props = cam._picam.camera_properties
        info["sensor_size"] = str(props.get("PixelArraySize", "unknown"))
        info["model"] = props.get("Model", "unknown")
    except Exception as e:
        info["properties_error"] = str(e)

    try:
        md = cam._picam.capture_metadata()
        info["AfState"] = md.get("AfState")
        info["LensPosition"] = md.get("LensPosition")
        info["ExposureTime"] = md.get("ExposureTime")
        info["AnalogueGain"] = md.get("AnalogueGain")
    except Exception as e:
        info["metadata_error"] = str(e)

    return info


# expose picamera2 availability for the trigger endpoint
try:
    from openscan_mini.controllers.hardware.camera import _PICAM2_AVAILABLE
except Exception:
    _PICAM2_AVAILABLE = False


@router.post("/exposure")
async def set_exposure(request: ExposureRequest) -> dict:
    _get().set_exposure(request.shutter_us)
    return {"shutter_us": request.shutter_us, "status": "ok"}


# ── Persistent settings ───────────────────────────────────────────────────────

class CameraSettingsUpdate(BaseModel):
    shutter_us: Optional[int] = Field(default=None, ge=100, le=1_000_000)
    gain: Optional[float] = Field(default=None, ge=0.1, le=16.0)
    jpeg_quality: Optional[int] = Field(default=None, ge=50, le=100)
    saturation: Optional[float] = Field(default=None, ge=0.0, le=4.0)
    contrast: Optional[float] = Field(default=None, ge=0.0, le=4.0)
    autofocus: Optional[bool] = None
    lens_position: Optional[float] = Field(default=None, ge=0.0, le=32.0)


@router.get("/settings")
async def get_camera_settings() -> dict:
    """Return current camera settings (live + persisted)."""
    cam = _get()
    s = cam.settings
    return {
        "shutter_us": s.shutter_us,
        "gain": s.gain,
        "jpeg_quality": s.jpeg_quality,
        "saturation": s.saturation,
        "contrast": s.contrast,
        "autofocus": s.autofocus,
        "lens_position": s.lens_position,
        "width": s.width,
        "height": s.height,
    }


@router.post("/settings")
async def update_camera_settings(request: CameraSettingsUpdate) -> dict:
    """Batch-update camera settings and persist to disk."""
    cam = _get()
    s = cam.settings

    if request.shutter_us is not None or request.gain is not None:
        cam.set_exposure(
            request.shutter_us if request.shutter_us is not None else s.shutter_us,
            request.gain,
        )
    if request.autofocus is not None:
        cam.set_autofocus(request.autofocus)
    if request.lens_position is not None and not s.autofocus:
        cam.set_focus(request.lens_position)
    if request.jpeg_quality is not None:
        s.jpeg_quality = request.jpeg_quality
    if request.saturation is not None:
        s.saturation = request.saturation
    if request.contrast is not None:
        s.contrast = request.contrast

    # Persist remaining fields that don't go through individual setters
    cam._persist_settings()

    return {"status": "ok", **await get_camera_settings()}
