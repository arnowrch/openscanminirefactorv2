"""3D path models for scan position representation."""

from dataclasses import dataclass
from enum import Enum


class PathMethod(str, Enum):
    SWEEP = "sweep"          # classic rotor-angle × turntable-step grid
    FIBONACCI = "fibonacci"  # Fibonacci sphere (golden angle distribution)


@dataclass(frozen=True)
class PolarPoint3D:
    """
    Spherical coordinate for a camera/object position.

    theta: elevation angle (0–180°)
        0°   = top pole (camera pointing straight down at object)
        90°  = equator (horizontal)
        180° = bottom pole
    fi:    azimuth angle (0–360°) — rotation around vertical axis
    r:     radius (default 1.0 for unit sphere)
    """
    theta: float
    fi: float
    r: float = 1.0
