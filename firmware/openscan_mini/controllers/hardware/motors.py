"""
Motor control for OpenScan Mini.

Provides high-level stepper motor control with acceleration ramping,
position tracking, and endstop detection.

Uses gpiozero for GPIO abstraction (works on Pi 4 with 64-bit OS).
"""

import asyncio
import logging
import time
from math import cos, pi
from typing import Optional

try:
    from gpiozero import OutputDevice
except ImportError:
    OutputDevice = None  # For testing on non-Pi systems

from openscan_mini.config.hardware import HardwareConfig, MotorConfig
from openscan_mini.models.motor import MotorDirection, MotorStatus


logger = logging.getLogger(__name__)


class MotorController:
    """
    Stepper motor controller with acceleration ramping and position tracking.

    Features:
    - Smooth acceleration/deceleration using cosine ramp
    - Real-time position tracking
    - Endstop detection (optional)
    - Software limits (min/max angle)
    - Async/await support
    """

    def __init__(
        self,
        motor_id: str,
        config: MotorConfig,
        enable_pin: Optional[int] = None,
    ):
        """
        Initialize a stepper motor controller.

        Args:
            motor_id: Identifier ('rotor' or 'turntable')
            config: MotorConfig from hardware.json
            enable_pin: Optional pin for motor enable (shared between motors)
        """
        self.motor_id = motor_id
        self.config = config
        self.enable_pin_number = enable_pin

        # Position tracking
        self.current_angle = 0.0
        self.target_angle = 0.0
        self.is_moving = False

        # Movement parameters
        self.speed = 0
        self.direction: Optional[MotorDirection] = None
        self.acceleration_profile = "cosine"  # 'linear', 'cosine', 'none'

        # GPIO setup
        self.step_pin: Optional[OutputDevice] = None
        self.dir_pin: Optional[OutputDevice] = None
        self.enable_pin: Optional[OutputDevice] = None

        # Endstop
        self.endstop_hit = False

        # Initialize GPIO if available
        self._init_gpio()

        logger.info(
            f"MotorController initialized: {motor_id}",
            extra={
                "motor": motor_id,
                "step_pin": config.step_pin,
                "dir_pin": config.dir_pin,
                "range": f"{config.min_angle}-{config.max_angle}°",
            },
        )

    def _init_gpio(self):
        """Initialize GPIO pins using gpiozero."""
        if OutputDevice is None:
            logger.warning(
                f"gpiozero not available (running on non-Pi system?). "
                f"GPIO simulation mode."
            )
            return

        try:
            self.step_pin = OutputDevice(self.config.step_pin)
            self.dir_pin = OutputDevice(self.config.dir_pin)

            if self.enable_pin_number:
                self.enable_pin = OutputDevice(self.enable_pin_number)

            logger.debug(
                f"GPIO pins initialized for {self.motor_id}",
                extra={
                    "step_pin": self.config.step_pin,
                    "dir_pin": self.config.dir_pin,
                    "enable_pin": self.enable_pin_number,
                },
            )
        except Exception as e:
            logger.error(f"Failed to initialize GPIO pins: {e}")
            raise

    async def move_to_angle(
        self,
        target_angle: float,
        wait_for_completion: bool = True,
        speed_override: Optional[int] = None,
    ) -> MotorStatus:
        """
        Move motor to target angle with acceleration ramping.

        Args:
            target_angle: Target position in degrees
            wait_for_completion: Block until movement complete
            speed_override: Override max speed (steps/sec)

        Returns:
            MotorStatus with final position

        Raises:
            ValueError: If angle out of range
        """
        # Validate angle
        if not (self.config.min_angle <= target_angle <= self.config.max_angle):
            raise ValueError(
                f"Angle {target_angle}° out of range "
                f"[{self.config.min_angle}, {self.config.max_angle}]"
            )

        self.target_angle = target_angle

        # Calculate movement parameters
        angle_delta = target_angle - self.current_angle
        steps = int(abs(angle_delta) / self.config.angle_per_step)

        if steps == 0:
            logger.debug(f"{self.motor_id} already at {target_angle}°")
            return self.get_status()

        # Determine direction
        direction = (
            MotorDirection.FORWARD if angle_delta > 0 else MotorDirection.BACKWARD
        )
        self.direction = direction

        # Set GPIO pin
        if self.dir_pin:
            direction_value = 1 if direction == MotorDirection.FORWARD else 0
            self.dir_pin.value = direction_value

        logger.info(
            f"{self.motor_id} moving to {target_angle}°",
            extra={
                "motor": self.motor_id,
                "from_angle": self.current_angle,
                "to_angle": target_angle,
                "steps": steps,
                "direction": direction.value,
            },
        )

        # Execute movement
        self.is_moving = True
        try:
            if wait_for_completion:
                await self._move_steps(steps, speed_override or self.config.max_speed_steps_sec)
            else:
                # Non-blocking: schedule async task
                asyncio.create_task(
                    self._move_steps(steps, speed_override or self.config.max_speed_steps_sec)
                )
        finally:
            self.is_moving = False

        logger.info(
            f"{self.motor_id} reached {self.current_angle}°",
            extra={"motor": self.motor_id, "position": self.current_angle},
        )

        return self.get_status()

    async def _move_steps(self, steps: int, max_speed: int):
        """
        Execute step sequence with acceleration ramping.

        Uses cosine curve for smooth acceleration:
        delay = delay_init * (1 + -1/acc * cos(ramp_progress) + 1/acc)

        Args:
            steps: Number of steps to execute
            max_speed: Maximum speed in steps/sec
        """
        ramp = self.config.acceleration_ramp
        delay_init = self.config.delay_base

        angle_per_step = self.config.angle_per_step
        direction_multiplier = 1 if self.direction == MotorDirection.FORWARD else -1

        for step_num in range(steps):
            # Calculate delay with acceleration ramping
            if step_num <= ramp and step_num <= steps / 2:
                # Acceleration phase
                cos_val = cos(1 * (ramp - step_num) / ramp * pi)
                delay = delay_init * (1 - 1 / 10 * cos_val + 1 / 10)
            elif steps - step_num <= ramp and step_num > steps / 2:
                # Deceleration phase
                cos_val = cos(1 * (ramp + step_num - steps) / ramp * pi)
                delay = delay_init * (1 - 1 / 10 * cos_val + 1 / 10)
            else:
                # Constant speed phase
                delay = delay_init

            # Execute step pulse
            if self.step_pin:
                self.step_pin.on()
                await asyncio.sleep(delay)
                self.step_pin.off()
                await asyncio.sleep(delay)
            else:
                # Simulation mode
                await asyncio.sleep(delay * 2)

            # Update position
            self.current_angle += angle_per_step * direction_multiplier

            # Log every 10% completion
            if step_num % max(1, steps // 10) == 0:
                logger.debug(
                    f"{self.motor_id} progress: {step_num}/{steps}",
                    extra={
                        "motor": self.motor_id,
                        "step": step_num,
                        "total": steps,
                        "angle": self.current_angle,
                    },
                )

    async def move_to_endstop(self, reverse: bool = False) -> MotorStatus:
        """
        Move motor until endstop is hit (homing).

        Args:
            reverse: If True, move in reverse direction first

        Returns:
            MotorStatus at endstop

        Raises:
            RuntimeError: If no endstop configured
        """
        if not hasattr(self, "endstop_pin") or self.endstop_pin is None:
            raise RuntimeError(f"{self.motor_id} has no endstop configured")

        logger.info(
            f"{self.motor_id} homing to endstop",
            extra={"motor": self.motor_id, "reverse": reverse},
        )

        direction = MotorDirection.BACKWARD if reverse else MotorDirection.FORWARD
        self.direction = direction

        # Set direction
        if self.dir_pin:
            dir_value = 1 if direction == MotorDirection.FORWARD else 0
            self.dir_pin.value = dir_value

        # Move until endstop hit (with timeout)
        max_steps = 50000  # Safety limit
        self.is_moving = True
        step_count = 0

        try:
            while step_count < max_steps:
                # Check endstop
                # TODO: Implement endstop detection

                # Execute step pulse
                if self.step_pin:
                    self.step_pin.on()
                    await asyncio.sleep(self.config.delay_base)
                    self.step_pin.off()
                    await asyncio.sleep(self.config.delay_base)

                step_count += 1

                if step_count % 1000 == 0:
                    logger.debug(
                        f"{self.motor_id} homing progress: {step_count} steps",
                        extra={"motor": self.motor_id, "steps": step_count},
                    )

        finally:
            self.is_moving = False

        # Set to endstop angle
        self.current_angle = (
            self.config.max_angle
            if direction == MotorDirection.FORWARD
            else self.config.min_angle
        )
        self.endstop_hit = True

        logger.info(
            f"{self.motor_id} homed successfully",
            extra={"motor": self.motor_id, "position": self.current_angle},
        )

        return self.get_status()

    def get_status(self) -> MotorStatus:
        """Get current motor status."""
        return MotorStatus(
            motor_id=self.motor_id,
            current_angle=round(self.current_angle, 2),
            target_angle=self.target_angle if self.is_moving else None,
            is_moving=self.is_moving,
            speed=self.config.max_speed_steps_sec if self.is_moving else 0,
            direction=self.direction,
            min_angle=self.config.min_angle,
            max_angle=self.config.max_angle,
            endstop_hit=self.endstop_hit,
            steps_per_rotation=self.config.steps_per_rotation,
            acceleration=self.config.acceleration_steps_sec2,
        )

    def cleanup(self):
        """Clean up GPIO resources."""
        if self.step_pin:
            self.step_pin.close()
        if self.dir_pin:
            self.dir_pin.close()
        if self.enable_pin:
            self.enable_pin.close()
        logger.info(f"{self.motor_id} GPIO resources cleaned up")


class DualMotorController:
    """
    Controller for dual stepper motors (rotor + turntable) with shared enable pin.

    Manages both motors simultaneously with synchronization support.
    """

    def __init__(self, config: HardwareConfig):
        """
        Initialize dual motor controller.

        Args:
            config: HardwareConfig instance
        """
        self.config = config
        self.rotor: MotorController | None = None
        self.turntable: MotorController | None = None

        # Initialize motors
        enable_pin = config.get_gpio_pin("motors.motor_enable")

        rotor_cfg = config.get_motor_config("rotor")
        self.rotor = MotorController("rotor", rotor_cfg, enable_pin=enable_pin)

        turntable_cfg = config.get_motor_config("turntable")
        self.turntable = MotorController("turntable", turntable_cfg, enable_pin=enable_pin)

        logger.info("DualMotorController initialized (rotor + turntable)")

    async def move_to_position(
        self,
        rotor_angle: float,
        turntable_angle: float,
        synchronous: bool = True,
    ) -> dict[str, MotorStatus]:
        """
        Move both motors to target positions.

        Args:
            rotor_angle: Target rotor angle (0-145°)
            turntable_angle: Target turntable angle (0-360°)
            synchronous: If True, wait for both motors; if False, move in parallel

        Returns:
            Status dict for both motors
        """
        logger.info(
            "Moving both motors to position",
            extra={
                "rotor_angle": rotor_angle,
                "turntable_angle": turntable_angle,
                "synchronous": synchronous,
            },
        )

        if synchronous:
            # Move both, wait for both
            await asyncio.gather(
                self.rotor.move_to_angle(rotor_angle),
                self.turntable.move_to_angle(turntable_angle),
            )
        else:
            # Non-blocking: schedule both
            asyncio.create_task(self.rotor.move_to_angle(rotor_angle))
            asyncio.create_task(self.turntable.move_to_angle(turntable_angle))

        return {
            "rotor": self.rotor.get_status(),
            "turntable": self.turntable.get_status(),
        }

    def get_status(self) -> dict[str, MotorStatus]:
        """Get status of both motors."""
        return {
            "rotor": self.rotor.get_status(),
            "turntable": self.turntable.get_status(),
        }

    def cleanup(self):
        """Clean up GPIO resources."""
        self.rotor.cleanup()
        self.turntable.cleanup()
        logger.info("DualMotorController cleaned up")
