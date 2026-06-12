"""
Scan path generation — ported from OpenScan3 utils/paths/.

Provides:
  generate_path()          — dispatcher for sweep or fibonacci
  generate_sweep_path()    — classic rotor × turntable grid
  generate_fibonacci_path() — Fibonacci sphere (golden angle)
  optimize_path_tsp()      — nearest-neighbor TSP optimizer
"""

import logging
import math
from typing import Optional

from openscan_mini.models.paths import PathMethod, PolarPoint3D
from openscan_mini.models.scan import ScanSetting

logger = logging.getLogger(__name__)

# Golden ratio conjugate used in phi distribution
_GRC = (math.sqrt(5) - 1) / 2


# ── Public API ────────────────────────────────────────────────────────────────

def generate_path(
    setting: ScanSetting,
    rotor_cfg: Optional[dict] = None,
    table_cfg: Optional[dict] = None,
) -> list[PolarPoint3D]:
    """
    Generate the ordered list of scan positions from a ScanSetting.

    If optimize_path=True and motor configs are provided, applies TSP
    nearest-neighbor ordering to minimize total movement time.
    """
    if setting.path_method == PathMethod.FIBONACCI:
        points = generate_fibonacci_path(setting)
    else:
        points = generate_sweep_path(setting)

    if setting.optimize_path and rotor_cfg and table_cfg and len(points) > 2:
        points = optimize_path_tsp(points, rotor_cfg, table_cfg)
        logger.info(f"TSP optimized {len(points)} points")

    logger.info(f"Path: {setting.path_method} — {len(points)} positions")
    return points


def generate_sweep_path(setting: ScanSetting) -> list[PolarPoint3D]:
    """
    Classic grid: for each rotor angle, full turntable revolution.

    Rotor angle → theta via: theta = rotor_angle + 90
    (rotor 0° = horizontal = theta 90°, rotor -20° = theta 70°, etc.)
    """
    points: list[PolarPoint3D] = []
    steps = max(1, setting.turntable_steps)
    fi_step = 360.0 / steps
    for rotor_angle in sorted(setting.rotor_angles):
        theta = rotor_angle + 90.0
        theta = max(0.0, min(180.0, theta))
        for step in range(steps):
            fi = (step * fi_step) % 360.0
            points.append(PolarPoint3D(theta=theta, fi=fi))
    return points


def generate_fibonacci_path(setting: ScanSetting) -> list[PolarPoint3D]:
    """
    Fibonacci sphere with golden-angle azimuth distribution.

    Z-range is derived from theta constraints:
      z = cos(theta), so min_theta→z_max, max_theta→z_min

    Phi is distributed using the golden ratio conjugate for even spacing,
    constrained to [min_phi, max_phi].
    """
    n = setting.points
    min_theta = math.radians(setting.min_theta)
    max_theta = math.radians(setting.max_theta)

    z_max = math.cos(min_theta)  # z at min elevation (near top)
    z_min = math.cos(max_theta)  # z at max elevation (near bottom)

    phi_span = (setting.max_phi - setting.min_phi) % 360
    if phi_span == 0:
        phi_span = 360.0

    points: list[PolarPoint3D] = []
    for i in range(n):
        # Linear z distribution in constrained range
        t = i / (n - 1) if n > 1 else 0.5
        z = z_min + (z_max - z_min) * t
        z = max(-1.0, min(1.0, z))
        theta = math.degrees(math.acos(z))

        # Golden-ratio phi distribution
        fi = (setting.min_phi + phi_span * (i * _GRC % 1.0)) % 360.0

        points.append(PolarPoint3D(theta=theta, fi=fi))

    return points


def optimize_path_tsp(
    points: list[PolarPoint3D],
    rotor_cfg: dict,
    table_cfg: dict,
    start: Optional[PolarPoint3D] = None,
) -> list[PolarPoint3D]:
    """
    Nearest-neighbor TSP ordering.

    Scores each candidate move by max(rotor_time, turntable_time) where
    both times are calculated using a trapezoidal motion profile matching
    the actual stepper acceleration.

    rotor_cfg / table_cfg keys: steps_per_rotation, acceleration_steps_sec2,
                                 max_speed_steps_sec
    """
    if not points:
        return []

    r_spr = rotor_cfg.get("steps_per_rotation", 42667)
    r_acc = rotor_cfg.get("acceleration_steps_sec2", 1000)
    r_spd = rotor_cfg.get("max_speed_steps_sec", 3000)

    t_spr = table_cfg.get("steps_per_rotation", 3200)
    t_acc = table_cfg.get("acceleration_steps_sec2", 2000)
    t_spd = table_cfg.get("max_speed_steps_sec", 6000)

    def move_time(degrees: float, spr: int, acc: float, spd: float) -> float:
        if degrees <= 0:
            return 0.0
        steps = int(abs(degrees) * spr / 360)
        if steps == 0:
            return 0.0
        accel_time = spd / acc
        accel_steps = int(0.5 * acc * accel_time ** 2)
        if 2 * accel_steps > steps:
            accel_steps = max(1, steps // 2)
            peak_time = math.sqrt(2 * accel_steps / acc)
            return 2 * peak_time
        const_steps = steps - 2 * accel_steps
        return accel_time + (const_steps / spd if const_steps > 0 else 0) + accel_time

    def score(a: PolarPoint3D, b: PolarPoint3D) -> float:
        rotor_deg = abs(b.theta - a.theta)
        # Turntable: shortest path with 360° wraparound
        direct = abs(b.fi - a.fi)
        table_deg = min(direct, 360.0 - direct)
        return max(
            move_time(rotor_deg, r_spr, r_acc, r_spd),
            move_time(table_deg, t_spr, t_acc, t_spd),
        )

    current = start or PolarPoint3D(theta=90.0, fi=0.0)
    unvisited = list(points)
    ordered: list[PolarPoint3D] = []

    while unvisited:
        best = min(unvisited, key=lambda p: score(current, p))
        ordered.append(best)
        unvisited.remove(best)
        current = best

    return ordered


def estimate_path_duration(
    points: list[PolarPoint3D],
    rotor_cfg: dict,
    table_cfg: dict,
    settle_ms: int = 300,
    capture_s: float = 2.0,
) -> float:
    """Estimate total scan time in seconds."""
    if not points:
        return 0.0
    total = 0.0
    prev = PolarPoint3D(theta=90.0, fi=0.0)
    for p in points:
        rotor_deg = abs(p.theta - prev.theta)
        direct = abs(p.fi - prev.fi)
        table_deg = min(direct, 360.0 - direct)
        # Simplified: assume linear at half max speed
        r_spd = rotor_cfg.get("max_speed_steps_sec", 3000)
        t_spd = table_cfg.get("max_speed_steps_sec", 6000)
        r_spr = rotor_cfg.get("steps_per_rotation", 42667)
        t_spr = table_cfg.get("steps_per_rotation", 3200)
        rt = (rotor_deg * r_spr / 360) / (r_spd * 0.5) if rotor_deg > 0 else 0
        tt = (table_deg * t_spr / 360) / (t_spd * 0.5) if table_deg > 0 else 0
        total += max(rt, tt) + settle_ms / 1000 + capture_s
        prev = p
    return round(total, 1)
