"""Scan configuration model."""

import uuid
from typing import Optional

from pydantic import BaseModel, Field

from openscan_mini.models.paths import PathMethod


class ScanSetting(BaseModel):
    """
    Unified scan configuration for both sweep and Fibonacci path methods.

    SWEEP mode uses rotor_angles × turntable_steps (the classic grid approach).
    FIBONACCI mode generates a Fibonacci sphere with N evenly-distributed points,
    optionally constrained to an elevation (theta) and azimuth (phi) range.
    """

    path_method: PathMethod = PathMethod.SWEEP

    # SWEEP params (backward compat)
    rotor_angles: list[float] = Field(default=[-20, 0, 20, 40])
    turntable_steps: int = Field(default=24, ge=1, le=720)

    # FIBONACCI params
    points: int = Field(default=130, ge=1, le=999)
    min_theta: float = Field(default=12.0, ge=0.0, le=180.0)
    max_theta: float = Field(default=125.0, ge=0.0, le=180.0)
    min_phi: float = Field(default=0.0, ge=0.0, le=360.0)
    max_phi: float = Field(default=360.0, ge=0.0, le=360.0)
    optimize_path: bool = True

    # Focus stacking
    focus_stacks: int = Field(default=1, ge=1, le=99)
    focus_range: tuple[float, float] = (8.0, 14.0)  # diopters

    # Common
    pause_before_capture_ms: int = Field(default=0, ge=0)
    settle_ms: int = Field(default=300, ge=0)
    project_name: str = "default"
    scan_id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()))
    image_format: str = "jpeg"
