"""REST API endpoints for UC-873 Rev.D USB camera control."""

import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hardware/camera", tags=["camera"])

CAMERA: "CameraController | None" = None  # type: ignore[name-defined]


def set_camera_controller(controller) -> None:
    global CAMERA
    CAMERA = controller


def _get():
    if not CAMERA:
        raise HTTPException(status_code=500, detail="Camera controller not initialized")
    return CAMERA


class FocusRequest(BaseModel):
    value: int = Field(..., ge=0, le=1023, description="Focus absolute 0–1023")


class ExposureRequest(BaseModel):
    shutter_us: int = Field(..., ge=100, le=1_000_000, description="Exposure in microseconds")


@router.get("/")
async def get_camera_status() -> dict:
    """Get camera status and current settings."""
    return _get().get_status()


@router.post("/capture")
async def capture_jpeg():
    """Capture a single JPEG frame and return it as image/jpeg."""
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
    """Set manual focus. Range 0–1023 (UC-873 default: 2838 clipped to 1023)."""
    _get().set_focus(request.value)
    return {"focus": request.value, "status": "ok"}


@router.post("/exposure")
async def set_exposure(request: ExposureRequest) -> dict:
    """Set exposure (shutter speed) in microseconds."""
    _get().set_exposure(request.shutter_us)
    return {"shutter_us": request.shutter_us, "status": "ok"}
