"""
FocusStackingTask — post-scan focus merge.

Non-exclusive: runs in the background after a multi-focus ScanTask completes.
Groups photos by (rotor_index, turntable_index), stacks each group with
FocusStacker, and writes a *_stacked.jpg alongside the source frames.
"""

import asyncio
import logging
import re
from pathlib import Path

from openscan_mini.services.focus_stacker import get_focus_stacker
from openscan_mini.services.project_manager import PROJECTS_DIR, ProjectManager, ScanRecord
from openscan_mini.services.task_manager import BaseTask, Task, TaskProgress

logger = logging.getLogger(__name__)

# Matches filenames like r00_t001_fs00.jpg  → groups: ri="00", ti="001"
_STACK_RE = re.compile(r"^r(\d+)_t(\d+)_fs\d+\.jpg$")


class FocusStackingTask(BaseTask):
    is_exclusive = False

    def __init__(
        self,
        task_model: Task,
        scan: ScanRecord,
        project_manager: ProjectManager,
    ):
        super().__init__(task_model)
        self._scan = scan
        self._pm = project_manager

    async def run(self):
        scan = self._scan
        scan_dir = PROJECTS_DIR / scan.project_name / f"scan{scan.index:02d}"

        # Collect all focus-stack frames and group by (ri, ti)
        groups: dict[tuple, list[Path]] = {}
        for p in sorted(scan_dir.glob("*.jpg")):
            m = _STACK_RE.match(p.name)
            if m:
                key = (m.group(1), m.group(2))
                groups.setdefault(key, []).append(p)

        if not groups:
            logger.info(f"FocusStackingTask: no stack frames found in {scan_dir}")
            return

        stacker = get_focus_stacker()
        total = len(groups)
        done = 0

        logger.info(
            f"FocusStackingTask: {scan.project_name}/scan{scan.index:02d} "
            f"— merging {total} positions"
        )

        for (ri, ti), frames in groups.items():
            if self.is_cancelled():
                break

            # Load raw bytes off-thread
            jpegs = await asyncio.to_thread(
                lambda fs=frames: [f.read_bytes() for f in fs]
            )

            # Stack (CPU-bound, off-thread)
            merged = await asyncio.to_thread(stacker.stack_jpegs, jpegs)

            out_name = f"r{ri}_t{ti}_stacked.jpg"
            out_path = scan_dir / out_name
            await asyncio.to_thread(out_path.write_bytes, merged)

            done += 1
            yield TaskProgress(
                current=done,
                total=total,
                message=f"Stacked {done}/{total} positions",
            )

        logger.info(
            f"FocusStackingTask done: {scan.project_name}/scan{scan.index:02d} "
            f"— {done}/{total} positions stacked"
        )
