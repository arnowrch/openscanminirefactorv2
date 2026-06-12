"""
GPIO abstraction layer for OpenScan Mini.

Adapted from OpenScan3 (github.com/OpenScan-org/OpenScan3).
Tracks initialized pins in module-level dicts to prevent GPIOPinInUse errors
when the same pin is referenced multiple times.
"""

import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_output_pins: Dict[int, object] = {}
_pwm_pins: Dict[int, object] = {}
_buttons: Dict[int, object] = {}

_SIMULATION = False  # set to True when gpiozero is unavailable

try:
    from gpiozero import Button, Device, DigitalOutputDevice, PWMOutputDevice

    # On Pi OS Bookworm, gpiozero may default to NativeFactory which has no
    # software-PWM support. Explicitly prefer lgpio (ships with Bookworm) so
    # PWMOutputDevice works on any GPIO pin (incl. GPIO17 which has no hw-PWM).
    try:
        from gpiozero.pins.lgpio import LGPIOFactory
        Device.pin_factory = LGPIOFactory()
        logger.info("GPIO: using lgpio pin factory (software-PWM on all pins)")
    except Exception as _lgpio_err:
        try:
            from gpiozero.pins.rpigpio import RPiGPIOFactory
            Device.pin_factory = RPiGPIOFactory()
            logger.info("GPIO: lgpio unavailable, using RPi.GPIO factory")
        except Exception:
            logger.warning(f"GPIO: could not set preferred factory ({_lgpio_err}), using default")

except ImportError:
    logger.warning("gpiozero not available — running in simulation mode")
    _SIMULATION = True
    DigitalOutputDevice = None
    PWMOutputDevice = None
    Button = None


def initialize_output_pins(pins: List[int]) -> None:
    if _SIMULATION:
        for pin in pins:
            _output_pins.setdefault(pin, None)
        return

    for pin in pins:
        if pin in _output_pins:
            logger.debug(f"Output pin {pin} already initialized, skipping.")
        elif pin in _pwm_pins:
            logger.error(f"Pin {pin} already initialized as PWM, cannot use as output.")
        elif pin in _buttons:
            logger.error(f"Pin {pin} already initialized as button, cannot use as output.")
        else:
            try:
                _output_pins[pin] = DigitalOutputDevice(pin, initial_value=False)
                logger.debug(f"Initialized GPIO{pin} as digital output.")
            except Exception as e:
                logger.error(f"Failed to initialize GPIO{pin} as output: {e}")
                _output_pins.pop(pin, None)


def set_output_pin(pin: int, value: bool, auto_initialize: bool = False) -> None:
    if _SIMULATION:
        return

    if pin not in _output_pins:
        if auto_initialize:
            initialize_output_pins([pin])
        else:
            logger.warning(f"GPIO{pin} not initialized — call initialize_output_pins first.")
            return

    dev = _output_pins.get(pin)
    if dev is not None:
        dev.value = value


def get_output_pin(pin: int) -> Optional[bool]:
    if _SIMULATION:
        return False
    dev = _output_pins.get(pin)
    return bool(dev.value) if dev is not None else None


def initialize_pwm_pins(pins: List[int], freq: int = 1000) -> None:
    if _SIMULATION:
        for pin in pins:
            _pwm_pins.setdefault(pin, None)
        return

    for pin in pins:
        if pin in _pwm_pins:
            logger.debug(f"PWM pin {pin} already initialized, skipping.")
        elif pin in _output_pins:
            logger.error(f"Pin {pin} already initialized as output, cannot use as PWM.")
        else:
            try:
                _pwm_pins[pin] = PWMOutputDevice(pin, active_high=True, initial_value=0.0, frequency=freq)
                logger.debug(f"Initialized GPIO{pin} as PWM output (freq={freq}Hz).")
            except Exception as e:
                logger.error(f"Failed to initialize GPIO{pin} as PWM: {e}")
                _pwm_pins.pop(pin, None)


def set_pwm_pin(pin: int, value: float) -> None:
    if _SIMULATION:
        return
    dev = _pwm_pins.get(pin)
    if dev is not None:
        dev.value = max(0.0, min(1.0, value))
    else:
        logger.warning(f"GPIO{pin} not initialized as PWM.")


def initialize_button(pin: int, pull_up: bool = True, bounce_time: float = 0.05) -> None:
    if _SIMULATION:
        _buttons.setdefault(pin, None)
        return

    if pin in _buttons:
        logger.debug(f"Button pin {pin} already initialized, skipping.")
        return
    if pin in _output_pins or pin in _pwm_pins:
        logger.error(f"Pin {pin} already in use as output/PWM, cannot use as button.")
        return
    try:
        _buttons[pin] = Button(pin, pull_up=pull_up, bounce_time=bounce_time)
        logger.debug(f"Initialized GPIO{pin} as button input.")
    except Exception as e:
        logger.error(f"Failed to initialize GPIO{pin} as button: {e}")
        _buttons.pop(pin, None)


def is_button_pressed(pin: int) -> Optional[bool]:
    if _SIMULATION:
        return False
    btn = _buttons.get(pin)
    return btn.is_pressed if btn is not None else None


def register_button_callback(pin: int, event_type: str, callback: Callable) -> None:
    btn = _buttons.get(pin)
    if btn is None:
        logger.error(f"Button GPIO{pin} not initialized.")
        return
    if event_type == "when_pressed":
        btn.when_pressed = callback
    elif event_type == "when_released":
        btn.when_released = callback
    else:
        logger.error(f"Unknown event_type '{event_type}' — use 'when_pressed' or 'when_released'.")


def get_initialized_pins() -> Dict[str, List[int]]:
    return {
        "output": list(_output_pins.keys()),
        "pwm": list(_pwm_pins.keys()),
        "buttons": list(_buttons.keys()),
    }


def cleanup_all_pins() -> None:
    for pin, dev in list(_output_pins.items()):
        try:
            if dev is not None:
                dev.close()
        except Exception as e:
            logger.error(f"Error closing GPIO{pin}: {e}")
    _output_pins.clear()

    for pin, dev in list(_pwm_pins.items()):
        try:
            if dev is not None:
                dev.close()
        except Exception as e:
            logger.error(f"Error closing PWM GPIO{pin}: {e}")
    _pwm_pins.clear()

    for pin, dev in list(_buttons.items()):
        try:
            if dev is not None:
                dev.close()
        except Exception as e:
            logger.error(f"Error closing button GPIO{pin}: {e}")
    _buttons.clear()

    logger.info("GPIO cleanup complete.")
