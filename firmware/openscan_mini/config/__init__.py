"""Configuration module for OpenScan Mini firmware."""

from .hardware import HardwareConfig
from .logger import setup_logging

__all__ = ["HardwareConfig", "setup_logging"]
