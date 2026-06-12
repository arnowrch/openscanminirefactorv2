"""REST API endpoints for ringlight control."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hardware/lights", tags=["lights"])

RINGLIGHT = None


def set_ringlight_controller(controller) -> None:
    global RINGLIGHT
    RINGLIGHT = controller


def _get():
    if not RINGLIGHT:
        raise HTTPException(status_code=500, detail="Ringlight controller not initialized")
    return RINGLIGHT


class BrightnessRequest(BaseModel):
    brightness: float = Field(..., ge=0, le=100, description="Brightness 0–100 %")


class ChannelRequest(BaseModel):
    pin: int = Field(..., description="GPIO pin number")
    brightness: float = Field(..., ge=0, le=100, description="Brightness 0–100 %")


@router.get("/")
async def get_ringlight_status() -> dict:
    """Get ringlight status and per-channel info."""
    return _get().get_status()


@router.post("/on")
async def ringlight_on(brightness: float = 100.0) -> dict:
    """Turn on all channels. brightness=0–100 (query param)."""
    return _get().turn_on(max(0.0, min(100.0, brightness)))


@router.post("/off")
async def ringlight_off() -> dict:
    """Turn off all channels."""
    return _get().turn_off()


@router.post("/brightness")
async def set_brightness(request: BrightnessRequest) -> dict:
    """Set brightness on all channels (0–100 %)."""
    return _get().set_brightness(request.brightness)


@router.post("/channel")
async def set_channel(request: ChannelRequest) -> dict:
    """
    Control a single GPIO pin independently.
    Useful for testing GPIO17 vs GPIO27 separately.
    Example: {"pin": 17, "brightness": 50}
    """
    try:
        return _get().set_channel(request.pin, request.brightness)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
