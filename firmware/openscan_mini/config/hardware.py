"""
Hardware configuration loader for OpenScan Mini.

Loads and validates GPIO, motor, camera, and ringlight configurations
from the hardware_greenshield.json file.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError


logger = logging.getLogger(__name__)


class MotorConfig(BaseModel):
    """Motor configuration (rotor or turntable)."""

    description: str
    step_pin: int
    dir_pin: int
    enable_pin: int
    steps_per_rotation: int
    angle_per_step: float
    max_speed_steps_sec: int
    max_angle_sec: float
    acceleration_steps_sec2: int
    min_angle: float
    max_angle: float
    direction: int = 1
    acceleration_ramp: int
    delay_base: float


class EndstopConfig(BaseModel):
    """Endstop switch configuration."""

    pin: int
    position_angle: float
    pull_up: bool
    debounce_ms: int
    normally_open: bool
    stop_on_trigger: bool


class CameraConfig(BaseModel):
    """Camera configuration (Arducam IMX519)."""

    type: str
    interface: str
    resolution: str
    sensor_size_mm: list[float]
    pixel_size_um: float
    lens: str
    autofocus: Dict[str, Any]
    capture_settings: Dict[str, Any]


class RinglightChannelConfig(BaseModel):
    """Single ringlight PWM channel configuration."""

    id: int
    gpio_pin: int
    pwm_capable: bool
    pwm_frequency_hz: int
    circuit: str
    components: list[str]
    control_range: list[int]
    brightness_mapping: str


class RinglightConfig(BaseModel):
    """Ringlight (LED) configuration."""

    type: str
    power: str
    control: str
    channels: list[RinglightChannelConfig]
    connector: str
    safety: Dict[str, Any]


class DeviceConfig(BaseModel):
    """Device metadata."""

    name: str
    model: str
    shield: str
    pi_model: str
    os: str
    notes: str


class HardwareConfig(BaseModel):
    """Complete hardware configuration."""

    device: DeviceConfig
    motors: Dict[str, MotorConfig]
    endstops: Optional[Dict[str, EndstopConfig]] = None
    camera: CameraConfig
    ringlight: RinglightConfig
    gpio_pins: Dict[str, Any]
    interfaces: Dict[str, Any]
    performance: Dict[str, Any]
    software: Dict[str, Any]

    class Config:
        """Pydantic config."""

        arbitrary_types_allowed = True

    @classmethod
    def from_json_file(cls, filepath: Path | str) -> "HardwareConfig":
        """
        Load hardware configuration from JSON file.

        Args:
            filepath: Path to hardware_greenshield.json

        Returns:
            HardwareConfig instance

        Raises:
            FileNotFoundError: If config file not found
            ValidationError: If config validation fails
        """
        filepath = Path(filepath)

        if not filepath.exists():
            raise FileNotFoundError(f"Hardware config not found: {filepath}")

        logger.info(f"Loading hardware config from {filepath}")

        try:
            with open(filepath, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in hardware config: {e}")
            raise

        try:
            config = cls(**data)
            logger.info(
                f"Hardware config loaded successfully: {config.device.name} "
                f"({config.device.shield})"
            )
            return config
        except ValidationError as e:
            logger.error(f"Hardware config validation failed: {e}")
            raise

    def get_motor_config(self, motor_name: str) -> MotorConfig:
        """
        Get configuration for a specific motor.

        Args:
            motor_name: 'rotor' or 'turntable'

        Returns:
            MotorConfig for the requested motor

        Raises:
            KeyError: If motor not found
        """
        if motor_name not in self.motors:
            available = ", ".join(self.motors.keys())
            raise KeyError(f"Motor '{motor_name}' not found. Available: {available}")
        return self.motors[motor_name]

    def get_endstop_config(self, endstop_name: str) -> EndstopConfig:
        """
        Get configuration for a specific endstop.

        Args:
            endstop_name: 'rotor', 'turntable', etc.

        Returns:
            EndstopConfig for the requested endstop

        Raises:
            KeyError: If endstop not found
        """
        if not self.endstops or endstop_name not in self.endstops:
            available = list(self.endstops.keys()) if self.endstops else []
            raise KeyError(f"Endstop '{endstop_name}' not found. Available: {available}")
        return self.endstops[endstop_name]

    def get_ringlight_channel(self, channel_id: int) -> RinglightChannelConfig:
        """
        Get configuration for a specific ringlight channel.

        Args:
            channel_id: Channel ID (typically 1 or 2)

        Returns:
            RinglightChannelConfig for the requested channel

        Raises:
            ValueError: If channel not found
        """
        for ch in self.ringlight.channels:
            if ch.id == channel_id:
                return ch
        raise ValueError(
            f"Ringlight channel {channel_id} not found. "
            f"Available channels: {[ch.id for ch in self.ringlight.channels]}"
        )

    def get_gpio_pin(self, pin_name: str) -> int:
        """
        Get GPIO pin number by name.

        Args:
            pin_name: Pin identifier (e.g., 'rotor_step', 'ringlight_ch1')

        Returns:
            GPIO pin number

        Raises:
            KeyError: If pin not found
        """
        parts = pin_name.split(".")
        current = self.gpio_pins

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise KeyError(
                    f"GPIO pin '{pin_name}' not found in configuration"
                )

        if isinstance(current, int):
            return current

        raise ValueError(f"GPIO pin '{pin_name}' does not map to an integer")

    def dump_summary(self) -> str:
        """
        Return a human-readable summary of the configuration.

        Returns:
            Formatted configuration summary
        """
        summary = f"""
OpenScan Mini Hardware Configuration Summary
{'='*50}

Device: {self.device.name}
  Shield: {self.device.shield}
  Pi Model: {self.device.pi_model}
  OS: {self.device.os}

Motors:
  Rotor:
    - Pins: step={self.motors['rotor'].step_pin}, dir={self.motors['rotor'].dir_pin}
    - Range: {self.motors['rotor'].min_angle}° – {self.motors['rotor'].max_angle}°
    - Speed: {self.motors['rotor'].max_speed_steps_sec} steps/sec

  Turntable:
    - Pins: step={self.motors['turntable'].step_pin}, dir={self.motors['turntable'].dir_pin}
    - Range: {self.motors['turntable'].min_angle}° – {self.motors['turntable'].max_angle}°
    - Speed: {self.motors['turntable'].max_speed_steps_sec} steps/sec

Camera: {self.camera.type}
  - Interface: {self.camera.interface}
  - Resolution: {self.camera.resolution}
  - Autofocus: {'Enabled' if self.camera.autofocus.get('enabled') else 'Disabled'}

Ringlight: {self.ringlight.type}
  - Power: {self.ringlight.power}
  - Channels: {len(self.ringlight.channels)}
  - Control Range: {self.ringlight.channels[0].control_range[0]}-{self.ringlight.channels[0].control_range[1]}

Software:
  - Framework: {self.software.get('framework')}
  - Python: {self.software.get('python_version')}
"""
        return summary
