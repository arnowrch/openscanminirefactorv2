"""REST API endpoints for Arducam IMX519 camera control."""

import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hardware/camera", tags=["camera"])

CAMERA = None


def set_camera_controller(controller) -> None:
    global CAMERA
    CAMERA = controller


def _get():
    if not CAMERA:
        raise HTTPException(status_code=500, detail="Camera controller not initialized")
    return CAMERA


class FocusRequest(BaseModel):
    lens_position: float = Field(
        ..., ge=0.0, le=32.0,
        description="Lens position in diopters: 0.0=infinity, ~2.0=50cm, ~10.0=10cm"
    )


class ExposureRequest(BaseModel):
    shutter_us: int = Field(..., ge=100, le=1_000_000, description="Shutter speed in microseconds")


class AutofocusRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable continuous autofocus")


@router.get("/")
async def get_camera_status() -> dict:
    """Get camera status and current settings."""
    return _get().get_status()


@router.get("/preview")
async def preview_jpeg():
    """Fast low-resolution preview (960×720, AF off). Used by live UI preview."""
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
    """Capture a single JPEG from the IMX519 and return as image/jpeg."""
    cam = _get()
    if cam._busy:
        raise HTTPException(status_code=503, detail="Camera busy")
    try:
        data = cam.capture_jpeg()
        return Response(content=data, media_type="image/jpeg")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/focus")
async def set_focus(request: FocusRequest) -> dict:
    """
    Set manual focus (disables autofocus).
    lens_position: 0.0=infinity, 2.0=50cm, 10.0=10cm (diopters = 1/distance_m).
    """
    _get().set_focus(request.lens_position)
    return {"lens_position": request.lens_position, "autofocus": False, "status": "ok"}


@router.post("/autofocus")
async def set_autofocus(request: AutofocusRequest) -> dict:
    """Enable or disable continuous autofocus."""
    _get().set_autofocus(request.enabled)
    return {"autofocus": request.enabled, "status": "ok"}


@router.post("/exposure")
async def set_exposure(request: ExposureRequest) -> dict:
    """Set exposure time in microseconds (e.g. 50000 = 50ms)."""
    _get().set_exposure(request.shutter_us)
    return {"shutter_us": request.shutter_us, "status": "ok"}
