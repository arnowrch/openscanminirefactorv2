"""
Ringlight controller for OpenScan Mini.

Controls onboard ringlight channels via PWM (gpiozero PWMOutputDevice).
Adapted from OpenScan3 (github.com/OpenScan-org/OpenScan3).

Hardware (Greenshield):
  Channel 1: GPIO17  — confirmed ringlight
  Channel 2: GPIO27  — ringlight OR endstop (needs physical test)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openscan_mini.controllers.hardware import gpio

logger = logging.getLogger(__name__)

PWM_FREQUENCY = 1000  # Hz


@dataclass
class ChannelState:
    pin: int
    is_on: bool = False
    brightness: float = 100.0  # 0-100 %


class RinglightController:
    """
    Controls one or more ringlight channels via PWM.

    Brightness is 0–100 %. All channels are driven in sync by default,
    but individual channels can be addressed for testing.
    """

    def __init__(self, pins: List[int], name: str = "ringlight"):
        self.name = name
        self.channels: Dict[int, ChannelState] = {
            pin: ChannelState(pin=pin) for pin in pins
        }

        gpio.initialize_pwm_pins(pins, freq=PWM_FREQUENCY)
        # Start with all channels off
        for pin in pins:
            gpio.set_pwm_pin(pin, 0.0)

        logger.info(f"RinglightController '{name}' ready — pins={pins}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def turn_on(self, brightness: Optional[float] = None) -> dict:
        """Turn on all channels. Optionally set brightness (0–100)."""
        for ch in self.channels.values():
            ch.is_on = True
            if brightness is not None:
                ch.brightness = max(0.0, min(100.0, brightness))
            gpio.set_pwm_pin(ch.pin, ch.brightness / 100.0)
        logger.info(f"Ringlight '{self.name}' ON — brightness={self._current_brightness():.0f}%")
        return self.get_status()

    def turn_off(self) -> dict:
        """Turn off all channels."""
        for ch in self.channels.values():
            ch.is_on = False
            gpio.set_pwm_pin(ch.pin, 0.0)
        logger.info(f"Ringlight '{self.name}' OFF")
        return self.get_status()

    def set_brightness(self, brightness: float) -> dict:
        """Set brightness (0–100 %) on all channels. Turns on if off."""
        brightness = max(0.0, min(100.0, brightness))
        for ch in self.channels.values():
            ch.brightness = brightness
            ch.is_on = brightness > 0
            gpio.set_pwm_pin(ch.pin, brightness / 100.0)
        logger.info(f"Ringlight '{self.name}' brightness={brightness:.0f}%")
        return self.get_status()

    def set_channel(self, pin: int, brightness: float) -> dict:
        """Set a single channel by pin number (for testing GPIO27)."""
        if pin not in self.channels:
            raise ValueError(f"Pin {pin} not in ringlight channels {list(self.channels)}")
        brightness = max(0.0, min(100.0, brightness))
        ch = self.channels[pin]
        ch.brightness = brightness
        ch.is_on = brightness > 0
        gpio.set_pwm_pin(pin, brightness / 100.0)
        logger.info(f"Ringlight '{self.name}' channel GPIO{pin}={brightness:.0f}%")
        return self.get_status()

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "is_on": any(ch.is_on for ch in self.channels.values()),
            "channels": [
                {
                    "pin": ch.pin,
                    "is_on": ch.is_on,
                    "brightness": ch.brightness,
                }
                for ch in self.channels.values()
            ],
        }

    def cleanup(self) -> None:
        self.turn_off()

    def _current_brightness(self) -> float:
        on_channels = [ch for ch in self.channels.values() if ch.is_on]
        return on_channels[0].brightness if on_channels else 0.0
