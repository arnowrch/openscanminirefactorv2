"""
Ringlight controller for OpenScan Mini.

Hardware (Greenshield verified from OS2 firmware):
  GPIO17 = ringlight1 (serienmäßig, confirmed)
  GPIO27 = ringlight2 AND rotor_endstop (conflict — use as endstop only)
  GPIO13 = external ringlight (hardware PWM pin, free, needs MOSFET circuit)

PWM strategy:
  - Try gpiozero PWMOutputDevice first (soft-PWM via lgpio)
  - Fall back to DigitalOutputDevice if PWM init fails
"""

import logging
from typing import Dict, List, Optional

from openscan_mini.controllers.hardware import gpio

logger = logging.getLogger(__name__)

PWM_FREQUENCY = 1000  # Hz


class ChannelState:
    def __init__(self, pin: int):
        self.pin = pin
        self.is_on = False
        self.brightness = 100.0  # 0–100 %
        self.pwm_available = False  # set True if PWMOutputDevice succeeded


class RinglightController:
    """
    Controls ringlight channels via PWM with digital fallback.

    Brightness 0–100 %. If PWM init fails on a pin, falls back to
    DigitalOutputDevice (on/off only) so hardware can be verified first.
    """

    def __init__(self, pins: List[int], name: str = "ringlight"):
        self.name = name
        self.channels: Dict[int, ChannelState] = {}

        for pin in pins:
            ch = ChannelState(pin)
            # Try PWM first
            try:
                gpio.initialize_pwm_pins([pin], freq=PWM_FREQUENCY)
                # Verify pin was actually registered
                initialized = gpio.get_initialized_pins()
                if pin in initialized.get("pwm", []):
                    ch.pwm_available = True
                    gpio.set_pwm_pin(pin, 0.0)
                    logger.info(f"GPIO{pin} initialized as PWM output.")
                else:
                    raise RuntimeError(f"PWM init silently failed for GPIO{pin}")
            except Exception as e:
                logger.warning(f"GPIO{pin} PWM failed ({e}), falling back to digital output.")
                gpio.initialize_output_pins([pin])
                gpio.set_output_pin(pin, False)

            self.channels[pin] = ch

        logger.info(
            f"RinglightController '{name}' ready — "
            f"pins={pins}, "
            f"pwm={[p for p, ch in self.channels.items() if ch.pwm_available]}, "
            f"digital={[p for p, ch in self.channels.items() if not ch.pwm_available]}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def turn_on(self, brightness: Optional[float] = None) -> dict:
        """Turn on all channels."""
        for ch in self.channels.values():
            if brightness is not None:
                ch.brightness = max(0.0, min(100.0, brightness))
            ch.is_on = True
            self._apply(ch)
        logger.info(f"Ringlight '{self.name}' ON — brightness={brightness or ch.brightness:.0f}%")
        return self.get_status()

    def turn_off(self) -> dict:
        """Turn off all channels."""
        for ch in self.channels.values():
            ch.is_on = False
            self._apply(ch)
        logger.info(f"Ringlight '{self.name}' OFF")
        return self.get_status()

    def set_brightness(self, brightness: float) -> dict:
        """Set brightness (0–100 %) on all channels."""
        brightness = max(0.0, min(100.0, brightness))
        for ch in self.channels.values():
            ch.brightness = brightness
            ch.is_on = brightness > 0
            self._apply(ch)
        return self.get_status()

    def set_channel(self, pin: int, brightness: float) -> dict:
        """Set a single channel by GPIO pin number."""
        if pin not in self.channels:
            raise ValueError(f"Pin {pin} not configured. Available: {list(self.channels)}")
        brightness = max(0.0, min(100.0, brightness))
        ch = self.channels[pin]
        ch.brightness = brightness
        ch.is_on = brightness > 0
        self._apply(ch)
        logger.info(f"Ringlight channel GPIO{pin}={brightness:.0f}%")
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
                    "mode": "pwm" if ch.pwm_available else "digital",
                }
                for ch in self.channels.values()
            ],
        }

    def cleanup(self) -> None:
        self.turn_off()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply(self, ch: ChannelState) -> None:
        """Write channel state to GPIO."""
        if ch.pwm_available:
            value = (ch.brightness / 100.0) if ch.is_on else 0.0
            gpio.set_pwm_pin(ch.pin, value)
        else:
            gpio.set_output_pin(ch.pin, ch.is_on)
