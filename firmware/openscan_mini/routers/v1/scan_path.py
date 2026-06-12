"""Scan path preview endpoint — generate positions without hardware movement."""

import logging

from fastapi import APIRouter

from openscan_mini.models.scan import ScanSetting
from openscan_mini.services.path_gen import estimate_path_duration, generate_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scan", tags=["scan"])

# Motor config cache — set from main.py after hardware init
_motor_cfg: dict = {}


def set_motor_config(rotor_cfg: dict, table_cfg: dict) -> None:
    global _motor_cfg
    _motor_cfg = {"rotor": rotor_cfg, "table": table_cfg}


@router.post("/path/preview")
async def preview_scan_path(setting: ScanSetting) -> dict:
    """
    Generate the scan path for a given ScanSetting without moving any hardware.

    Returns the list of positions (theta/fi in degrees), total count, and
    estimated duration. Useful for the UI to visualise sphere coverage.
    """
    rotor_cfg = _motor_cfg.get("rotor", {})
    table_cfg = _motor_cfg.get("table", {})

    points = generate_path(setting, rotor_cfg or None, table_cfg or None)

    estimated_s = estimate_path_duration(
        points, rotor_cfg, table_cfg,
        settle_ms=setting.settle_ms,
    )

    return {
        "method": setting.path_method,
        "count": len(points),
        "points": [{"theta": round(p.theta, 2), "fi": round(p.fi, 2)} for p in points],
        "estimated_duration_s": estimated_s,
        "estimated_duration_min": round(estimated_s / 60, 1),
        "focus_stacks": setting.focus_stacks,
        "total_captures": len(points) * max(1, setting.focus_stacks),
    }
