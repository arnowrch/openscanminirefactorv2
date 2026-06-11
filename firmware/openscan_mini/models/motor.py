"""
Motor models and status definitions.

Pydantic models for motor configuration, state, and API responses.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MotorDirection(str, Enum):
    """Motor rotation direction."""

    FORWARD = "forward"
    BACKWARD = "backward"


class MotorStatus(BaseModel):
    """Current motor state and position."""

    motor_id: str = Field(..., description="Motor identifier (rotor, turntable)")
    current_angle: float = Field(..., description="Current position in degrees")
    target_angle: Optional[float] = Field(None, description="Target position (if moving)")
    is_moving: bool = Field(False, description="Whether motor is currently moving")
    speed: float = Field(default=0.0, description="Current speed in steps/sec")
    direction: Optional[MotorDirection] = Field(None, description="Current direction")
    min_angle: float = Field(..., description="Minimum allowed angle")
    max_angle: float = Field(..., description="Maximum allowed angle")
    endstop_hit: bool = Field(False, description="Whether endstop was hit")
    steps_per_rotation: int = Field(..., description="Steps per 360° rotation")
    acceleration: int = Field(..., description="Acceleration in steps/sec²")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "motor_id": "rotor",
                "current_angle": 45.2,
                "target_angle": 90.0,
                "is_moving": True,
                "speed": 2500,
                "direction": "forward",
                "min_angle": 0,
                "max_angle": 145,
                "endstop_hit": False,
                "steps_per_rotation": 10240,
                "acceleration": 10000,
            }
        }


class MotorMoveRequest(BaseModel):
    """Request to move a motor to a target angle."""

    angle: float = Field(..., description="Target angle in degrees", ge=0, le=360)
    speed: Optional[int] = Field(None, description="Override max speed (steps/sec)")
    acceleration: Optional[int] = Field(None, description="Override acceleration (steps/sec²)")
    wait_for_completion: bool = Field(default=True, description="Block until movement complete")

    class Config:
        """Pydantic config."""

        json_schema_extra = {"example": {"angle": 45.0, "wait_for_completion": True}}


class MotorHomeRequest(BaseModel):
    """Request to home a motor to its endstop."""

    motor_id: str = Field(..., description="Motor to home (rotor, turntable)")
    reverse: bool = Field(
        default=False, description="Home in reverse direction (if no endstop)"
    )
    max_steps: Optional[int] = Field(
        None, description="Max steps before timeout (safety limit)"
    )

    class Config:
        """Pydantic config."""

        json_schema_extra = {"example": {"motor_id": "rotor", "reverse": False}}


class MotorListResponse(BaseModel):
    """Response listing all motors and their current status."""

    motors: dict[str, MotorStatus] = Field(..., description="Status for each motor")
    timestamp: float = Field(..., description="Response timestamp (Unix)")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "motors": {
                    "rotor": {
                        "motor_id": "rotor",
                        "current_angle": 0.0,
                        "is_moving": False,
                        "min_angle": 0,
                        "max_angle": 145,
                    },
                    "turntable": {
                        "motor_id": "turntable",
                        "current_angle": 0.0,
                        "is_moving": False,
                        "min_angle": 0,
                        "max_angle": 360,
                    },
                },
                "timestamp": 1718092800.123,
            }
        }


class MotorCalibrationData(BaseModel):
    """Motor calibration and identity data."""

    motor_id: str
    min_angle: float
    max_angle: float
    steps_per_rotation: int
    angle_per_step: float
    max_speed: int  # steps/sec
    acceleration: int  # steps/sec²
    is_calibrated: bool = True
    calibration_timestamp: Optional[float] = None
