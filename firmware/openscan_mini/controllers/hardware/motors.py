"""
Stepper motor controller for OpenScan Mini.

Trapezoidal acceleration profile adapted from OpenScan3
(github.com/OpenScan-org/OpenScan3).

Each motor owns its own enable, step, and direction pins.
GPIO is managed via the gpio module (prevents GPIOPinInUse on shared pins).
"""

import asyncio
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from openscan_mini.config.hardware import EndstopConfig, HardwareConfig, MotorConfig
from openscan_mini.controllers.hardware import gpio
from openscan_mini.models.motor import MotorDirection, MotorStatus

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)

STEP_PULSE_WIDTH = 10e-6  # 10 µs HIGH pulse per step
MIN_STEP_INTERVAL = 100e-6  # 100 µs minimum between steps


HOMING_SPEED = 800   # steps/sec — slow and safe for endstop approach


class MotorController:
    """
    Single stepper motor controller with trapezoidal acceleration.

    Position is tracked in degrees. GPIO is initialized once via the
    module-level gpio dict — safe to call multiple times for shared pins.
    """

    def __init__(self, motor_id: str, config: MotorConfig,
                 endstop: Optional[EndstopConfig] = None):
        self.motor_id = motor_id
        self.config = config
        self._endstop = endstop

        self.current_angle: float = 0.0
        self.is_moving: bool = False
        self._stop_requested: bool = False
        self._endstop_hit: bool = False

        gpio.initialize_output_pins([
            config.step_pin,
            config.dir_pin,
            config.enable_pin,
        ])
        # Motor starts disabled (enable is active-low on A4988/DRV8825)
        gpio.set_output_pin(config.enable_pin, True)

        if endstop:
            gpio.initialize_button(
                endstop.pin,
                pull_up=endstop.pull_up,
                bounce_time=endstop.debounce_ms / 1000,
            )
            logger.info(f"MotorController '{motor_id}' endstop: GPIO{endstop.pin} "
                        f"(pos={endstop.position_angle}°)")

        logger.info(
            f"MotorController '{motor_id}' ready — "
            f"step={config.step_pin}, dir={config.dir_pin}, "
            f"enable={config.enable_pin}, spr={config.steps_per_rotation}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def move_to(self, target_angle: float, check_endstop: bool = False) -> MotorStatus:
        """Move to absolute angle (degrees). Clamps to [min_angle, max_angle].

        check_endstop=False (default): endstop is ignored during move/jog.
        check_endstop=True: used only by home() — stops on endstop trigger.
        """
        target_angle = max(self.config.min_angle, min(self.config.max_angle, target_angle))

        delta = target_angle - self.current_angle
        steps = int(abs(delta) * self.config.steps_per_rotation / 360)

        if steps == 0:
            return self.get_status()

        direction = 1 if delta > 0 else -1
        await self._execute(steps, direction, target_angle, check_endstop=check_endstop)
        return self.get_status()

    async def move_by(self, degrees: float) -> MotorStatus:
        """Move relative by degrees. Endstop not checked (jog use-case)."""
        return await self.move_to(self.current_angle + degrees, check_endstop=False)

    def stop(self) -> None:
        self._stop_requested = True

    def is_at_endstop(self) -> bool:
        if not self._endstop:
            return False
        pressed = gpio.is_button_pressed(self._endstop.pin)
        return bool(pressed)

    async def home(self) -> MotorStatus:
        """
        Drive slowly toward min_angle until endstop fires.
        Resets current_angle to endstop.position_angle on trigger.
        Safe to call without endstop — just moves to min_angle.
        """
        if not self._endstop:
            logger.warning(f"{self.motor_id}: no endstop configured — moving to min_angle")
            return await self.move_to(self.config.min_angle)

        if self.is_at_endstop():
            logger.info(f"{self.motor_id}: already at endstop")
            self.current_angle = self._endstop.position_angle
            return self.get_status()

        self.is_moving = True
        self._stop_requested = False
        self._endstop_hit = False

        # Direction toward min_angle
        direction = -1 if self.config.min_angle < self.current_angle else 1
        # Homing steps = full range + 20% margin
        total_range = abs(self.config.max_angle - self.config.min_angle)
        max_steps = int((total_range * 1.2) * self.config.steps_per_rotation / 360)
        angle_per_step = 360.0 / self.config.steps_per_rotation * direction

        gpio.set_output_pin(self.config.dir_pin, direction > 0)
        gpio.set_output_pin(self.config.enable_pin, False)

        loop = asyncio.get_event_loop()
        try:
            hit = await loop.run_in_executor(
                _executor,
                self._run_homing_steps,
                max_steps,
                angle_per_step,
            )
            if hit:
                self.current_angle = self._endstop.position_angle
                logger.info(f"{self.motor_id}: homed at {self.current_angle}°")
            else:
                logger.warning(f"{self.motor_id}: endstop not hit during homing")
        finally:
            self.is_moving = False
            gpio.set_output_pin(self.config.enable_pin, True)

        return self.get_status()

    def get_status(self) -> MotorStatus:
        return MotorStatus(
            motor_id=self.motor_id,
            current_angle=round(self.current_angle, 3),
            target_angle=None,
            is_moving=self.is_moving,
            speed=self.config.max_speed_steps_sec if self.is_moving else 0,
            direction=None,
            min_angle=self.config.min_angle,
            max_angle=self.config.max_angle,
            endstop_hit=self.is_at_endstop(),
            steps_per_rotation=self.config.steps_per_rotation,
            acceleration=self.config.acceleration_steps_sec2,
        )

    def cleanup(self) -> None:
        gpio.set_output_pin(self.config.enable_pin, True)  # disable motor

    # ------------------------------------------------------------------
    # Internal movement
    # ------------------------------------------------------------------

    def _pre_calculate_step_times(self, steps: int) -> List[float]:
        """
        Return list of absolute timestamps (seconds from move start)
        for each step pulse, using a trapezoidal speed profile.
        """
        accel = self.config.acceleration_steps_sec2
        max_speed = self.config.max_speed_steps_sec

        accel_time = max_speed / accel
        accel_steps = int(0.5 * accel * accel_time ** 2)

        if 2 * accel_steps > steps:
            # Triangular profile — never reach max speed
            accel_steps = max(1, steps // 2)
            peak_time = math.sqrt(2 * accel_steps / accel)
            peak_speed = accel * peak_time
        else:
            peak_speed = max_speed
            peak_time = accel_time

        const_steps = steps - 2 * accel_steps

        times: List[float] = []

        def t_accel(s: int) -> float:
            return math.sqrt(2 * s / accel)

        # Acceleration phase
        for s in range(1, accel_steps + 1):
            times.append(t_accel(s))

        # Constant speed phase
        if const_steps > 0:
            interval = 1.0 / peak_speed
            t_const_start = peak_time
            for i in range(const_steps):
                times.append(t_const_start + i * interval)

        # Deceleration phase (mirror of acceleration)
        t_decel_start = times[-1] if times else 0.0
        for s in range(accel_steps, 0, -1):
            dt = t_accel(s + 1) - t_accel(s)
            dt = max(dt, MIN_STEP_INTERVAL)
            t_decel_start += dt
            times.append(t_decel_start)

        # Enforce minimum interval
        for i in range(1, len(times)):
            if times[i] - times[i - 1] < MIN_STEP_INTERVAL:
                times[i] = times[i - 1] + MIN_STEP_INTERVAL

        return times

    async def _execute(self, steps: int, direction: int, target_angle: float,
                       check_endstop: bool = False) -> None:
        self.is_moving = True
        self._stop_requested = False
        angle_per_step = 360.0 / self.config.steps_per_rotation * direction

        # Set direction pin
        gpio.set_output_pin(self.config.dir_pin, direction > 0)
        # Enable motor driver
        gpio.set_output_pin(self.config.enable_pin, False)

        step_times = self._pre_calculate_step_times(steps)
        loop = asyncio.get_event_loop()

        try:
            await loop.run_in_executor(
                _executor, self._run_steps, step_times, angle_per_step, check_endstop
            )
        finally:
            self.is_moving = False
            # Snap to exact target to avoid float drift
            self.current_angle = target_angle
            # Disable motor (saves power, reduces heat)
            gpio.set_output_pin(self.config.enable_pin, True)

    def _run_steps(self, step_times: List[float], angle_per_step: float,
                   check_endstop: bool = False) -> None:
        """Blocking step execution — runs in ThreadPoolExecutor."""
        import time

        step_pin = self.config.step_pin
        endstop_pin = self._endstop.pin if self._endstop else None
        t_start = time.monotonic()
        executed = 0

        for t_target in step_times:
            if self._stop_requested:
                break

            # Only check endstop during homing — always-triggered pins block all jog otherwise
            if check_endstop and endstop_pin and gpio.is_button_pressed(endstop_pin):
                logger.info(f"{self.motor_id}: endstop hit at {self.current_angle:.1f}°")
                self._endstop_hit = True
                break

            # Busy-wait for precise timing (last 1 ms), sleep for the rest
            t_now = time.monotonic() - t_start
            remaining = t_target - t_now
            if remaining > 0.001:
                time.sleep(remaining - 0.001)
            while time.monotonic() - t_start < t_target:
                pass

            # Step pulse
            gpio.set_output_pin(step_pin, True)
            time.sleep(STEP_PULSE_WIDTH)
            gpio.set_output_pin(step_pin, False)

            self.current_angle += angle_per_step
            executed += 1

        logger.debug(f"{self.motor_id}: executed {executed}/{len(step_times)} steps")

    def _run_homing_steps(self, max_steps: int, angle_per_step: float) -> bool:
        """Slow homing run — steps until endstop fires or max_steps reached."""
        import time

        step_pin = self.config.step_pin
        endstop_pin = self._endstop.pin if self._endstop else None
        interval = 1.0 / HOMING_SPEED

        for _ in range(max_steps):
            if self._stop_requested:
                return False
            if endstop_pin and gpio.is_button_pressed(endstop_pin):
                return True

            gpio.set_output_pin(step_pin, True)
            time.sleep(STEP_PULSE_WIDTH)
            gpio.set_output_pin(step_pin, False)
            self.current_angle += angle_per_step
            time.sleep(interval - STEP_PULSE_WIDTH)

        return False


class DualMotorController:
    """Manages rotor + turntable motors. Each motor uses its own enable pin."""

    def __init__(self, config: HardwareConfig):
        rotor_cfg = config.get_motor_config("rotor")
        turntable_cfg = config.get_motor_config("turntable")

        # Wire endstops from hardware config
        # endstop1 (GPIO24) → rotor arm, endstop2 (GPIO25) → turntable
        endstops = {k: v for k, v in config.endstops.items()} if hasattr(config, 'endstops') else {}
        rotor_endstop = endstops.get("endstop1")
        table_endstop = endstops.get("endstop2")

        self.rotor = MotorController("rotor", rotor_cfg, endstop=rotor_endstop)
        self.turntable = MotorController("turntable", turntable_cfg, endstop=table_endstop)

        logger.info("DualMotorController ready (rotor + turntable)")

    def get_status(self) -> dict:
        return {
            "rotor": self.rotor.get_status(),
            "turntable": self.turntable.get_status(),
        }

    async def move_to_position(
        self,
        rotor_angle: float,
        turntable_angle: float,
        parallel: bool = True,
    ) -> dict:
        if parallel:
            await asyncio.gather(
                self.rotor.move_to(rotor_angle),
                self.turntable.move_to(turntable_angle),
            )
        else:
            await self.rotor.move_to(rotor_angle)
            await self.turntable.move_to(turntable_angle)

        return self.get_status()

    def cleanup(self) -> None:
        gpio.cleanup_all_pins()
        logger.info("DualMotorController cleaned up")
