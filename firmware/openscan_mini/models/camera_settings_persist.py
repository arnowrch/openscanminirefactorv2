"""Pydantic model for persisted camera settings (survives restarts)."""

from pydantic import BaseModel


class CameraSettingsPersist(BaseModel):
    shutter_us: int = 50000
    gain: float = 1.0
    jpeg_quality: int = 95
    saturation: float = 1.0
    contrast: float = 1.0
    autofocus: bool = True
    lens_position: float = 0.0
    width: int = 4656
    height: int = 3496
