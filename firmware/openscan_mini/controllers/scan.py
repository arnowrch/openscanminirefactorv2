"""
Scan engine for OpenScan Mini.

Executes a full 3D scan: moves rotor through configured angles,
sweeps turntable 360° at each position, captures one photo per step.

Scan sequence:
  for each rotor_angle:
    move rotor → rotor_angle
    for each turntable step (0..360):
      move turntable → step_angle
      wait settle_ms
      capture JPEG → save to output_dir
      broadcast progress via WebSocket
  move rotor back to 0°
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class ScanState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScanConfig:
    rotor_angles: List[float]       # e.g. [-20, 0, 20, 40]
    turntable_steps: int = 24       # photos per rotor position (360/steps per photo)
    settle_ms: int = 300            # wait after motor move before capture
    scan_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    output_dir: str = "/home/pi/openscan-scans"

    @property
    def turntable_angle_step(self) -> float:
        return 360.0 / self.turntable_steps

    @property
    def total_photos(self) -> int:
        return len(self.rotor_angles) * self.turntable_steps


@dataclass
class ScanProgress:
    scan_id: str
    state: ScanState
    total: int
    captured: int
    current_rotor: float
    current_turntable: float
    current_file: str
    elapsed_s: float
    eta_s: Optional[float]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "state": self.state.value,
            "total": self.total,
            "captured": self.captured,
            "percent": round(self.captured / self.total * 100, 1) if self.total else 0,
            "current_rotor": self.current_rotor,
            "current_turntable": self.current_turntable,
            "current_file": self.current_file,
            "elapsed_s": round(self.elapsed_s, 1),
            "eta_s": round(self.eta_s, 1) if self.eta_s is not None else None,
            "error": self.error,
        }


class ScanEngine:
    """
    Orchestrates rotor + turntable motors and camera for a full scan.

    Usage:
        engine = ScanEngine(motor_controller, camera_controller)
        await engine.start(config)
        # monitor via engine.progress or WebSocket broadcasts
    """

    def __init__(self, motor_controller, camera_controller):
        self._motors = motor_controller
        self._camera = camera_controller
        self._state = ScanState.IDLE
        self._task: Optional[asyncio.Task] = None
        self._config: Optional[ScanConfig] = None
        self._progress: Optional[ScanProgress] = None
        self._broadcast_callbacks: List[Callable] = []
        self._cancel_requested = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_broadcast_callback(self, cb: Callable) -> None:
        """Register a callback(progress_dict) for WebSocket broadcast."""
        self._broadcast_callbacks.append(cb)

    def remove_broadcast_callback(self, cb: Callable) -> None:
        self._broadcast_callbacks.discard(cb) if hasattr(self._broadcast_callbacks, 'discard') else None
        if cb in self._broadcast_callbacks:
            self._broadcast_callbacks.remove(cb)

    async def start(self, config: ScanConfig) -> ScanProgress:
        if self._state == ScanState.RUNNING:
            raise RuntimeError("Scan already running.")

        self._config = config
        self._cancel_requested = False
        self._state = ScanState.RUNNING
        self._progress = ScanProgress(
            scan_id=config.scan_id,
            state=ScanState.RUNNING,
            total=config.total_photos,
            captured=0,
            current_rotor=0.0,
            current_turntable=0.0,
            current_file="",
            elapsed_s=0.0,
            eta_s=None,
        )
        self._task = asyncio.create_task(self._run(config))
        return self._progress

    def stop(self) -> None:
        """Request cancellation of the running scan."""
        if self._state == ScanState.RUNNING:
            self._cancel_requested = True
            logger.info("Scan cancellation requested.")

    def get_progress(self) -> Optional[dict]:
        if self._progress is None:
            return {"state": ScanState.IDLE.value}
        return self._progress.to_dict()

    # ------------------------------------------------------------------
    # Internal scan loop
    # ------------------------------------------------------------------

    async def _run(self, config: ScanConfig) -> None:
        start_time = time.monotonic()
        output_path = Path(config.output_dir) / config.scan_id
        output_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Scan '{config.scan_id}' started — {config.total_photos} photos → {output_path}")

        try:
            for r_idx, rotor_angle in enumerate(config.rotor_angles):
                if self._cancel_requested:
                    break

                # Move rotor
                logger.info(f"Rotor → {rotor_angle}°")
                await self._motors.rotor.move_to(rotor_angle)
                self._update_progress(rotor_angle, 0.0, "", start_time)

                for t_idx in range(config.turntable_steps):
                    if self._cancel_requested:
                        break

                    turntable_angle = t_idx * config.turntable_angle_step

                    # Move turntable
                    await self._motors.turntable.move_to(turntable_angle)

                    # Settle
                    if config.settle_ms > 0:
                        await asyncio.sleep(config.settle_ms / 1000.0)

                    # Capture
                    filename = f"r{r_idx:02d}_t{t_idx:03d}.jpg"
                    filepath = output_path / filename
                    try:
                        jpeg_bytes = await asyncio.to_thread(self._camera.capture_jpeg)
                        filepath.write_bytes(jpeg_bytes)
                        logger.debug(f"Saved {filename} ({len(jpeg_bytes):,} bytes)")
                    except Exception as e:
                        logger.error(f"Capture failed at {filename}: {e}")
                        # Non-fatal: continue scan, log the gap
                        filename = f"{filename}.FAILED"

                    self._progress.captured += 1
                    self._update_progress(rotor_angle, turntable_angle, filename, start_time)
                    await self._broadcast()

            # Return rotor to 0
            if not self._cancel_requested:
                logger.info("Scan complete — returning rotor to 0°")
                await self._motors.rotor.move_to(0.0)
                self._state = ScanState.DONE
            else:
                self._state = ScanState.CANCELLED
                logger.info("Scan cancelled.")

        except Exception as e:
            self._state = ScanState.FAILED
            self._progress.error = str(e)
            logger.error(f"Scan failed: {e}", exc_info=True)
        finally:
            self._progress.state = self._state
            self._progress.elapsed_s = time.monotonic() - start_time
            self._progress.eta_s = None
            await self._broadcast()
            logger.info(f"Scan '{config.scan_id}' finished: {self._state.value}, "
                        f"{self._progress.captured}/{config.total_photos} photos, "
                        f"{output_path}")

    def _update_progress(
        self, rotor: float, turntable: float, filename: str, start_time: float
    ) -> None:
        p = self._progress
        p.state = self._state
        p.current_rotor = rotor
        p.current_turntable = turntable
        p.current_file = filename
        p.elapsed_s = time.monotonic() - start_time

        if p.captured > 0 and p.total > 0:
            rate = p.captured / p.elapsed_s  # photos/sec
            remaining = p.total - p.captured
            p.eta_s = remaining / rate if rate > 0 else None
        else:
            p.eta_s = None

    async def _broadcast(self) -> None:
        if not self._broadcast_callbacks or self._progress is None:
            return
        data = self._progress.to_dict()
        for cb in list(self._broadcast_callbacks):
            try:
                await cb(data)
            except Exception:
                pass
