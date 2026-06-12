"""REST API endpoints for motor control."""

import logging
import time

from fastapi import APIRouter, HTTPException

from openscan_mini.models.motor import MotorListResponse, MotorMoveRequest, MotorStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hardware/motors", tags=["motors"])

MOTOR_CONTROLLER = None


def set_motor_controller(controller) -> None:
    global MOTOR_CONTROLLER
    MOTOR_CONTROLLER = controller


def _get_controller():
    if not MOTOR_CONTROLLER:
        raise HTTPException(status_code=500, detail="Motor controller not initialized")
    return MOTOR_CONTROLLER


def _get_motor(motor_id: str):
    ctrl = _get_controller()
    if motor_id == "rotor":
        return ctrl.rotor
    if motor_id == "turntable":
        return ctrl.turntable
    raise HTTPException(status_code=400, detail=f"Unknown motor '{motor_id}'. Use 'rotor' or 'turntable'.")


@router.get("", response_model=MotorListResponse)
async def get_all_motors() -> MotorListResponse:
    """Get status of all motors."""
    motors = _get_controller().get_status()
    return MotorListResponse(motors=motors, timestamp=time.time())


@router.get("/{motor_id}", response_model=MotorStatus)
async def get_motor_status(motor_id: str) -> MotorStatus:
    """Get status of a specific motor."""
    return _get_motor(motor_id).get_status()


@router.post("/{motor_id}/move", response_model=MotorStatus)
async def move_motor(motor_id: str, request: MotorMoveRequest) -> MotorStatus:
    """Move a motor to an absolute target angle."""
    motor = _get_motor(motor_id)
    try:
        return await motor.move_to(request.angle)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Motor {motor_id} move failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/both/move")
async def move_both_motors(
    rotor_angle: float = 0.0,
    turntable_angle: float = 0.0,
    parallel: bool = True,
) -> dict:
    """Move both motors to target angles (parallel by default)."""
    ctrl = _get_controller()
    try:
        return await ctrl.move_to_position(rotor_angle, turntable_angle, parallel=parallel)
    except Exception as e:
        logger.error(f"Dual motor move failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{motor_id}/jog", response_model=MotorStatus)
async def jog_motor(motor_id: str, delta: float = 0.0) -> MotorStatus:
    """Jog motor by delta degrees relative to current position."""
    motor = _get_motor(motor_id)
    try:
        return await motor.move_by(delta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_motors() -> dict:
    """Move both motors to 0°."""
    ctrl = _get_controller()
    try:
        await ctrl.move_to_position(0.0, 0.0)
        return {"status": "ok", "message": "All motors reset to 0°"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
