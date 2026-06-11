"""
OpenScan Mini — High-Performance 3D Scanner Firmware

A refactored, modern FastAPI-based firmware for the OpenScan Mini 3D scanner,
targeting Raspberry Pi 4 with 64-bit Bookworm OS.

Features:
  - Hardware control via gpiozero (motors, ringlight, endstops)
  - Arducam IMX519 camera integration with autofocus
  - ArUco-based position tracking and homing
  - Real-time WebSocket preview and control
  - Heimnetz integration for distributed photogrammetry
  - Professional architecture with tests and documentation

Version: 0.2.0-dev
License: GPL-3.0
"""

__version__ = "0.2.0-dev"
__author__ = "OpenScan Mini Refactor"
__email__ = "dev@openscan-mini.local"

import logging

# Configure root logger
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
