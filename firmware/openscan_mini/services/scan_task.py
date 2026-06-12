"""
ScanTask — the main scan orchestrator as a BaseTask async generator.

Replaces the old ScanEngine with a task-system-compatible implementation
that supports pause/resume/cancel and integrates with ProjectManager.
"""

import asyncio
import logging
import math
from typing import Optional

from openscan_mini.models.scan import ScanSetting
from openscan_mini.services.project_manager import ProjectManager, ScanRecord, get_project_manager
from openscan_mini.services.task_manager import BaseTask, Task, TaskProgress, TaskStatus

logger = logging.getLogger(__name__)


class ScanTask(BaseTask):
    """
    Exclusive scan task: orchestrates motors, camera, and ringlight.

    Implements pause/resume at each capture position, cancellation on demand,
    and writes photos via ProjectManager for proper project/scan hierarchy.

    Coordinate mapping:
      PolarPoint3D.theta → rotor angle via: rotor_deg = theta - 90
        theta=90° (equator) → rotor at 0° (horizontal)
        theta=70° → rotor at -20°
        theta=130° → rotor at +40°
      PolarPoint3D.fi → turntable angle directly (0–360°)
    """

    is_exclusive = True

    def __init__(
        self,
        task_model: Task,
        settings: ScanSetting,
        motors,        # DualMotorController
        camera,        # CameraController
        ringlight,     # RinglightController | None
        project_manager: Optional[ProjectManager] = None,
    ):
        super().__init__(task_model)
        self._settings = settings
        self._motors = motors
        self._camera = camera
        self._ringlight = ringlight
        self._pm = project_manager or get_project_manager()

    async def run(self):
        """Async generator — yields TaskProgress at each captured position."""
        settings = self._settings
        pm = self._pm

        # 1. Create scan record in project hierarchy
        scan: ScanRecord = await asyncio.to_thread(pm.create_scan, settings.project_name, settings)
        self._task_model.run_kwargs["scan_index"] = scan.index
        self._task_model.run_kwargs["project_name"] = settings.project_name

        # 2. Generate scan path
        from openscan_mini.services.path_gen import generate_path
        from openscan_mini.routers.v1.scan_path import _motor_cfg

        rotor_cfg = _motor_cfg.get("rotor", {})
        table_cfg = _motor_cfg.get("table", {})
        points = generate_path(settings, rotor_cfg or None, table_cfg or None)

        n_points = len(points)
        focus_n = max(1, settings.focus_stacks)
        total_captures = n_points * focus_n
        scan.total_steps = total_captures
        await asyncio.to_thread(pm.save_scan_state, scan)

        logger.info(
            f"ScanTask: project={settings.project_name} scan={scan.index:02d} "
            f"points={n_points} stacks={focus_n} total={total_captures}"
        )

        # 3. Ringlight on
        if self._ringlight:
            try:
                self._ringlight.turn_on()
            except Exception as e:
                logger.warning(f"Ringlight on failed: {e}")

        step = 0
        turntable_steps = max(1, settings.turntable_steps)

        try:
            for i, point in enumerate(points):
                # ── Pause/Cancel checkpoint ──────────────────────────
                await self.wait_for_pause()
                if self.is_cancelled():
                    break

                # ── Optional pre-move pause ──────────────────────────
                if settings.pause_before_capture_ms > 0:
                    await asyncio.sleep(settings.pause_before_capture_ms / 1000)

                # ── Move motors (rotor + turntable concurrently) ─────
                rotor_angle = point.theta - 90.0  # polar theta → rotor degrees
                turntable_angle = point.fi

                try:
                    await asyncio.gather(
                        self._motors.rotor.move_to(rotor_angle),
                        self._motors.turntable.move_to(turntable_angle),
                    )
                except Exception as e:
                    logger.error(f"Motor move failed at position {i}: {e}")
                    scan.status = TaskStatus.ERROR
                    await asyncio.to_thread(pm.save_scan_state, scan)
                    raise

                # ── Settle ───────────────────────────────────────────
                if settings.settle_ms > 0:
                    await asyncio.sleep(settings.settle_ms / 1000)

                # ── Capture ──────────────────────────────────────────
                # Compute human-readable indices for filenames
                if settings.path_method.value == "sweep":
                    ri = i // turntable_steps
                    ti = i % turntable_steps
                else:
                    ri = 0   # fibonacci: flat numbering
                    ti = i

                if focus_n <= 1:
                    # Single capture
                    jpeg = await asyncio.to_thread(self._camera.capture_jpeg)
                    fname = f"r{ri:02d}_t{ti:03d}.jpg"
                    await pm.add_photo_async(scan, fname, jpeg)
                    step += 1
                    yield TaskProgress(
                        current=step,
                        total=total_captures,
                        message=f"Position {i + 1}/{n_points}",
                    )
                else:
                    # Focus stack: sweep focus range
                    focus_min, focus_max = settings.focus_range
                    focus_positions = _linspace(focus_min, focus_max, focus_n)

                    for fi_idx, fpos in enumerate(focus_positions):
                        await self.wait_for_pause()
                        if self.is_cancelled():
                            break
                        try:
                            await asyncio.to_thread(self._camera.set_focus, fpos)
                        except Exception:
                            pass
                        jpeg = await asyncio.to_thread(self._camera.capture_jpeg)
                        fname = f"r{ri:02d}_t{ti:03d}_fs{fi_idx:02d}.jpg"
                        await pm.add_photo_async(scan, fname, jpeg)
                        step += 1
                        yield TaskProgress(
                            current=step,
                            total=total_captures,
                            message=f"Position {i + 1}/{n_points} · Focus {fi_idx + 1}/{focus_n}",
                        )

                    # Restore autofocus after stack
                    if self._camera.settings.autofocus:
                        try:
                            await asyncio.to_thread(self._camera.set_autofocus, True)
                        except Exception:
                            pass

                if self.is_cancelled():
                    break

                scan.current_step = i + 1
                await asyncio.to_thread(pm.save_scan_state, scan)

        finally:
            # Return rotor to 0° (non-blocking, best-effort)
            try:
                await self._motors.rotor.move_to(0.0)
            except Exception:
                pass

            # Ringlight off
            if self._ringlight:
                try:
                    self._ringlight.turn_off()
                except Exception:
                    pass

            # Final scan status
            if self.is_cancelled():
                scan.status = TaskStatus.CANCELLED
            else:
                scan.status = TaskStatus.COMPLETED
                scan.completed_at = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
            await asyncio.to_thread(pm.save_scan_state, scan)

            logger.info(
                f"ScanTask done: {scan.project_name}/scan{scan.index:02d} "
                f"status={scan.status} photos={len(scan.photos)}"
            )

        # Queue focus stacking task if needed
        if (
            not self.is_cancelled()
            and focus_n > 1
            and len(scan.photos) > 0
        ):
            await _queue_stacking_task(scan, pm)


async def _queue_stacking_task(scan: ScanRecord, pm: ProjectManager) -> None:
    """Queue a FocusStackingTask after a multi-focus scan completes."""
    try:
        from openscan_mini.services.focus_stacking_task import FocusStackingTask
        from openscan_mini.services.task_manager import Task, get_task_manager

        task_model = Task(name="focus_stacking_task")
        instance = FocusStackingTask(task_model=task_model, scan=scan, project_manager=pm)
        tm = get_task_manager()
        queued = await tm.create_and_run_task(instance)
        scan.focus_task_id = queued.id
        await asyncio.to_thread(pm.save_scan_state, scan)
        logger.info(f"Queued FocusStackingTask {queued.id[:8]} for {scan.project_name}/scan{scan.index:02d}")
    except ImportError:
        logger.info("Focus stacking not available (opencv not installed)")
    except Exception as e:
        logger.warning(f"Could not queue stacking task: {e}")


def _linspace(start: float, stop: float, n: int) -> list[float]:
    if n <= 1:
        return [start]
    return [start + (stop - start) * i / (n - 1) for i in range(n)]
