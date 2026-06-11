"""
REST API endpoints for motor control.

Provides endpoints for moving motors, querying status, and homing.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from openscan_mini.models.motor import (
    MotorListResponse,
    MotorMoveRequest,
    MotorStatus,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hardware/motors", tags=["motors"])

# Global motor controller (set by main.py on startup)
MOTOR_CONTROLLER = None


def set_motor_controller(controller):
    """Register the motor controller (called from main.py)."""
    global MOTOR_CONTROLLER
    MOTOR_CONTROLLER = controller


@router.get("", response_model=MotorListResponse)
async def get_all_motors() -> MotorListResponse:
    """
    Get status of all motors.

    Returns:
        Status dict with rotor and turntable positions
    """
    if not MOTOR_CONTROLLER:
        raise HTTPException(status_code=500, detail="Motor controller not initialized")

    motors = MOTOR_CONTROLLER.get_status()

    return MotorListResponse(
        motors=motors,
        timestamp=__import__("time").time(),
    )


@router.get("/{motor_id}", response_model=MotorStatus)
async def get_motor_status(motor_id: str) -> MotorStatus:
    """
    Get status of a specific motor.

    Args:
        motor_id: 'rotor' or 'turntable'

    Returns:
        Motor status and current position
    """
    if not MOTOR_CONTROLLER:
        raise HTTPException(status_code=500, detail="Motor controller not initialized")

    if motor_id not in ["rotor", "turntable"]:
        raise HTTPException(status_code=400, detail=f"Unknown motor: {motor_id}")

    motors = MOTOR_CONTROLLER.get_status()
    return motors[motor_id]


@router.post("/{motor_id}/move", response_model=MotorStatus)
async def move_motor(motor_id: str, request: MotorMoveRequest) -> MotorStatus:
    """
    Move a motor to a target angle.

    Args:
        motor_id: 'rotor' or 'turntable'
        request: Movement parameters (angle, speed, etc.)

    Returns:
        Motor status after movement
    """
    if not MOTOR_CONTROLLER:
        raise HTTPException(status_code=500, detail="Motor controller not initialized")

    if motor_id not in ["rotor", "turntable"]:
        raise HTTPException(status_code=400, detail=f"Unknown motor: {motor_id}")

    try:
        if motor_id == "rotor":
            status = await MOTOR_CONTROLLER.rotor.move_to_angle(
                request.angle,
                wait_for_completion=request.wait_for_completion,
                speed_override=request.speed,
            )
        else:  # turntable
            status = await MOTOR_CONTROLLER.turntable.move_to_angle(
                request.angle,
                wait_for_completion=request.wait_for_completion,
                speed_override=request.speed,
            )

        logger.info(
            f"Motor {motor_id} moved successfully",
            extra={
                "motor": motor_id,
                "angle": request.angle,
                "final_position": status.current_angle,
            },
        )

        return status

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Motor move failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rotor/move", response_model=MotorStatus)
async def move_rotor(request: MotorMoveRequest) -> MotorStatus:
    """
    Move rotor to target angle.

    Args:
        request: Movement parameters

    Returns:
        Final motor status
    """
    return await move_motor("rotor", request)


@router.post("/turntable/move", response_model=MotorStatus)
async def move_turntable(request: MotorMoveRequest) -> MotorStatus:
    """
    Move turntable to target angle.

    Args:
        request: Movement parameters

    Returns:
        Final motor status
    """
    return await move_motor("turntable", request)


@router.post("/{motor_id}/home", response_model=MotorStatus)
async def home_motor(motor_id: str) -> MotorStatus:
    """
    Home a motor to its endstop.

    Args:
        motor_id: 'rotor' or 'turntable'

    Returns:
        Motor status at endstop
    """
    if not MOTOR_CONTROLLER:
        raise HTTPException(status_code=500, detail="Motor controller not initialized")

    if motor_id not in ["rotor", "turntable"]:
        raise HTTPException(status_code=400, detail=f"Unknown motor: {motor_id}")

    try:
        if motor_id == "rotor":
            status = await MOTOR_CONTROLLER.rotor.move_to_endstop()
        else:
            status = await MOTOR_CONTROLLER.turntable.move_to_endstop()

        logger.info(
            f"Motor {motor_id} homed successfully",
            extra={"motor": motor_id, "position": status.current_angle},
        )

        return status

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Motor homing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rotor/home", response_model=MotorStatus)
async def home_rotor() -> MotorStatus:
    """Home rotor to endstop."""
    return await home_motor("rotor")


@router.post("/turntable/home", response_model=MotorStatus)
async def home_turntable() -> MotorStatus:
    """Home turntable to endstop."""
    return await home_motor("turntable")


@router.post("/both/move", response_model=dict)
async def move_both_motors(
    rotor_angle: float = 0.0,
    turntable_angle: float = 0.0,
    synchronous: bool = True,
) -> dict:
    """
    Move both motors simultaneously.

    Args:
        rotor_angle: Target rotor angle (0-145°)
        turntable_angle: Target turntable angle (0-360°)
        synchronous: Move both at same time (True) or one after other (False)

    Returns:
        Status of both motors
    """
    if not MOTOR_CONTROLLER:
        raise HTTPException(status_code=500, detail="Motor controller not initialized")

    try:
        status = await MOTOR_CONTROLLER.move_to_position(
            rotor_angle=rotor_angle,
            turntable_angle=turntable_angle,
            synchronous=synchronous,
        )

        logger.info(
            "Both motors moved successfully",
            extra={
                "rotor_angle": rotor_angle,
                "turntable_angle": turntable_angle,
                "actual_rotor": status["rotor"].current_angle,
                "actual_turntable": status["turntable"].current_angle,
            },
        )

        return {
            "rotor": status["rotor"],
            "turntable": status["turntable"],
        }

    except Exception as e:
        logger.error(f"Dual motor move failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_motors() -> dict:
    """
    Reset all motors to known position (0°).

    Safe operation: moves slowly with homing if available.

    Returns:
        Confirmation message
    """
    if not MOTOR_CONTROLLER:
        raise HTTPException(status_code=500, detail="Motor controller not initialized")

    try:
        logger.info("Resetting all motors to 0°")

        await MOTOR_CONTROLLER.move_to_position(
            rotor_angle=0.0,
            turntable_angle=0.0,
            synchronous=True,
        )

        return {"status": "ok", "message": "All motors reset to 0°"}

    except Exception as e:
        logger.error(f"Motor reset failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
