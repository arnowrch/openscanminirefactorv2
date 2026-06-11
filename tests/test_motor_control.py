"""
Unit tests for motor controller.

Tests MotorController and DualMotorController with mocked GPIO.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from openscan_mini.config.hardware import HardwareConfig, MotorConfig
from openscan_mini.controllers.hardware.motors import MotorController, DualMotorController
from openscan_mini.models.motor import MotorDirection, MotorStatus, MotorMoveRequest


@pytest.fixture
def hardware_config():
    """Load hardware config from JSON."""
    config_path = Path(__file__).parent.parent / "configs" / "hardware_greenshield.json"
    return HardwareConfig.from_json_file(config_path)


@pytest.fixture
def rotor_config(hardware_config):
    """Get rotor config."""
    return hardware_config.get_motor_config("rotor")


@pytest.fixture
def turntable_config(hardware_config):
    """Get turntable config."""
    return hardware_config.get_motor_config("turntable")


class TestMotorController:
    """Test suite for MotorController."""

    def test_initialization(self, rotor_config):
        """Test motor controller initialization."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)

            assert motor.motor_id == "rotor"
            assert motor.current_angle == 0.0
            assert motor.target_angle == 0.0
            assert motor.is_moving is False

    def test_get_status(self, rotor_config):
        """Test status reporting."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)
            status = motor.get_status()

            assert isinstance(status, MotorStatus)
            assert status.motor_id == "rotor"
            assert status.current_angle == 0.0
            assert status.is_moving is False
            assert status.min_angle == rotor_config.min_angle
            assert status.max_angle == rotor_config.max_angle

    def test_angle_validation(self, rotor_config):
        """Test angle range validation."""
        with patch("openscan_mi.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)

            # Valid angles should not raise
            with pytest.raises(ValueError):
                asyncio.run(motor.move_to_angle(-10))

            with pytest.raises(ValueError):
                asyncio.run(motor.move_to_angle(200))

    @pytest.mark.asyncio
    async def test_move_to_angle(self, rotor_config):
        """Test moving to a target angle."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)

            # Move to 45 degrees
            status = await motor.move_to_angle(45.0)

            assert status.motor_id == "rotor"
            assert abs(status.current_angle - 45.0) < 0.1  # Allow small error
            assert status.is_moving is False

    @pytest.mark.asyncio
    async def test_move_no_steps_needed(self, rotor_config):
        """Test move when already at target."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)

            # Move to 0 (already there)
            status = await motor.move_to_angle(0.0)

            assert status.current_angle == 0.0
            assert status.is_moving is False

    @pytest.mark.asyncio
    async def test_direction_forward(self, rotor_config):
        """Test forward direction."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)

            await motor.move_to_angle(45.0)

            assert motor.direction == MotorDirection.FORWARD

    @pytest.mark.asyncio
    async def test_direction_backward(self, rotor_config):
        """Test backward direction."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)

            # Move forward first
            await motor.move_to_angle(45.0)
            assert motor.direction == MotorDirection.FORWARD

            # Move backward
            await motor.move_to_angle(10.0)
            assert motor.direction == MotorDirection.BACKWARD

    @pytest.mark.asyncio
    async def test_sequential_moves(self, rotor_config):
        """Test multiple sequential moves."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            motor = MotorController("rotor", rotor_config)

            positions = [30.0, 60.0, 45.0, 90.0, 0.0]

            for target in positions:
                status = await motor.move_to_angle(target)
                assert abs(status.current_angle - target) < 0.1

    def test_cleanup(self, rotor_config):
        """Test GPIO cleanup."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice") as mock_output:
            motor = MotorController("rotor", rotor_config)

            # Create mock GPIO objects
            motor.step_pin = Mock()
            motor.dir_pin = Mock()
            motor.enable_pin = Mock()

            motor.cleanup()

            # Verify close() was called
            motor.step_pin.close.assert_called_once()
            motor.dir_pin.close.assert_called_once()
            motor.enable_pin.close.assert_called_once()


class TestDualMotorController:
    """Test suite for DualMotorController."""

    def test_initialization(self, hardware_config):
        """Test dual motor controller initialization."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            dual = DualMotorController(hardware_config)

            assert dual.rotor is not None
            assert dual.turntable is not None
            assert dual.rotor.motor_id == "rotor"
            assert dual.turntable.motor_id == "turntable"

    def test_get_status(self, hardware_config):
        """Test dual motor status."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            dual = DualMotorController(hardware_config)
            status = dual.get_status()

            assert "rotor" in status
            assert "turntable" in status
            assert status["rotor"].motor_id == "rotor"
            assert status["turntable"].motor_id == "turntable"

    @pytest.mark.asyncio
    async def test_move_to_position(self, hardware_config):
        """Test moving both motors."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            dual = DualMotorController(hardware_config)

            status = await dual.move_to_position(
                rotor_angle=45.0,
                turntable_angle=180.0,
                synchronous=True,
            )

            assert abs(status["rotor"].current_angle - 45.0) < 0.1
            assert abs(status["turntable"].current_angle - 180.0) < 0.1

    @pytest.mark.asyncio
    async def test_move_both_at_limits(self, hardware_config):
        """Test moving to angle limits."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            dual = DualMotorController(hardware_config)

            status = await dual.move_to_position(
                rotor_angle=145.0,  # Max for rotor
                turntable_angle=360.0,  # Max for turntable
                synchronous=True,
            )

            assert abs(status["rotor"].current_angle - 145.0) < 0.1
            assert abs(status["turntable"].current_angle - 360.0) < 0.1

    def test_cleanup(self, hardware_config):
        """Test dual motor cleanup."""
        with patch("openscan_mini.controllers.hardware.motors.OutputDevice"):
            dual = DualMotorController(hardware_config)

            # Mock cleanup methods
            dual.rotor.cleanup = Mock()
            dual.turntable.cleanup = Mock()

            dual.cleanup()

            dual.rotor.cleanup.assert_called_once()
            dual.turntable.cleanup.assert_called_once()


class TestMotorModels:
    """Test Pydantic models."""

    def test_motor_status_model(self):
        """Test MotorStatus model."""
        status = MotorStatus(
            motor_id="rotor",
            current_angle=45.2,
            is_moving=False,
            min_angle=0,
            max_angle=145,
            endstop_hit=False,
            steps_per_rotation=10240,
            acceleration=10000,
        )

        assert status.motor_id == "rotor"
        assert status.current_angle == 45.2
        assert status.is_moving is False

    def test_motor_move_request_model(self):
        """Test MotorMoveRequest model."""
        request = MotorMoveRequest(angle=45.0, wait_for_completion=True)

        assert request.angle == 45.0
        assert request.wait_for_completion is True
        assert request.speed is None

    def test_motor_move_request_with_speed(self):
        """Test MotorMoveRequest with speed override."""
        request = MotorMoveRequest(angle=45.0, speed=3000)

        assert request.angle == 45.0
        assert request.speed == 3000

    def test_motor_direction_enum(self):
        """Test MotorDirection enum."""
        assert MotorDirection.FORWARD.value == "forward"
        assert MotorDirection.BACKWARD.value == "backward"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
