"""
Project and scan record management.

Directory layout:
  ~/openscan-projects/{project_name}/project.json
  ~/openscan-projects/{project_name}/scan{index:02d}/scan.json
  ~/openscan-projects/{project_name}/scan{index:02d}/{filename}.jpg
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from openscan_mini.models.scan import ScanSetting
from openscan_mini.services.task_manager import TaskStatus

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path.home() / "openscan-projects"
LEGACY_SCANS_DIR = Path.home() / "openscan-scans"


# ── Data Models ──────────────────────────────────────────────────────────────

class ScanRecord(BaseModel):
    project_name: str
    index: int
    scan_id: str
    created: datetime
    status: str = "running"         # TaskStatus value as string
    settings: ScanSetting
    current_step: int = 0
    total_steps: int = 0
    photos: list[str] = Field(default_factory=list)  # relative paths
    task_id: Optional[str] = None
    focus_task_id: Optional[str] = None
    completed_at: Optional[datetime] = None


class Project(BaseModel):
    name: str
    path: str
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""
    scan_count: int = 0             # denormalized for fast listing


# ── ProjectManager Singleton ─────────────────────────────────────────────────

class ProjectManager:
    _instance: Optional["ProjectManager"] = None

    def __new__(cls) -> "ProjectManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"ProjectManager initialized — {PROJECTS_DIR}")

    # ── Projects ─────────────────────────────────────────────────────────

    def get_or_create_project(self, name: str, description: str = "") -> Project:
        project_dir = PROJECTS_DIR / name
        meta_file = project_dir / "project.json"

        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text())
                data["scan_count"] = len(list(project_dir.glob("scan*/scan.json")))
                return Project.model_validate(data)
            except Exception as e:
                logger.warning(f"Could not load project '{name}': {e}")

        project_dir.mkdir(parents=True, exist_ok=True)
        project = Project(name=name, path=str(project_dir), description=description)
        meta_file.write_text(project.model_dump_json(indent=2))
        logger.info(f"Created project '{name}'")
        return project

    def list_projects(self) -> list[dict]:
        """Return list of projects with their scans summary."""
        projects = []

        # New project-based scans
        for project_dir in sorted(PROJECTS_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            meta_file = project_dir / "project.json"
            scans = []
            for scan_dir in sorted(project_dir.glob("scan*")):
                scan_json = scan_dir / "scan.json"
                if scan_json.exists():
                    try:
                        s = json.loads(scan_json.read_text())
                        photos = list(scan_dir.glob("*.jpg"))
                        scans.append({
                            "index": s.get("index", 0),
                            "scan_id": s.get("scan_id", ""),
                            "status": s.get("status", "unknown"),
                            "created": s.get("created", ""),
                            "photo_count": len(photos),
                            "current_step": s.get("current_step", 0),
                            "total_steps": s.get("total_steps", 0),
                            "settings": s.get("settings", {}),
                            "task_id": s.get("task_id"),
                            "focus_task_id": s.get("focus_task_id"),
                        })
                    except Exception:
                        pass

            if meta_file.exists():
                try:
                    p = json.loads(meta_file.read_text())
                    projects.append({
                        "name": p.get("name", project_dir.name),
                        "description": p.get("description", ""),
                        "created": p.get("created", ""),
                        "scans": scans,
                        "scan_count": len(scans),
                    })
                    continue
                except Exception:
                    pass

            # directory exists but no project.json
            if scans:
                projects.append({
                    "name": project_dir.name,
                    "description": "",
                    "created": "",
                    "scans": scans,
                    "scan_count": len(scans),
                })

        # Legacy flat scans under "legacy" project
        if LEGACY_SCANS_DIR.exists():
            legacy_scans = []
            for scan_dir in sorted(LEGACY_SCANS_DIR.iterdir()):
                if scan_dir.is_dir():
                    photos = list(scan_dir.glob("*.jpg"))
                    if photos:
                        legacy_scans.append({
                            "index": None,
                            "scan_id": scan_dir.name,
                            "status": "completed",
                            "created": "",
                            "photo_count": len(photos),
                            "current_step": len(photos),
                            "total_steps": len(photos),
                            "settings": {},
                            "task_id": None,
                        })
            if legacy_scans:
                projects.append({
                    "name": "legacy",
                    "description": "Legacy flat scans (~/openscan-scans/)",
                    "created": "",
                    "scans": legacy_scans,
                    "scan_count": len(legacy_scans),
                })

        return projects

    # ── Scans ────────────────────────────────────────────────────────────

    def create_scan(self, project_name: str, settings: ScanSetting) -> ScanRecord:
        """Create scan directory + scan.json, return ScanRecord."""
        project = self.get_or_create_project(project_name)
        project_dir = Path(project.path)

        # Find next index
        existing = sorted(project_dir.glob("scan*/scan.json"))
        index = len(existing)
        scan_dir = project_dir / f"scan{index:02d}"
        scan_dir.mkdir(parents=True, exist_ok=True)

        record = ScanRecord(
            project_name=project_name,
            index=index,
            scan_id=settings.scan_id or f"{project_name}-scan{index:02d}",
            created=datetime.now(timezone.utc),
            status="running",
            settings=settings,
        )
        (scan_dir / "scan.json").write_text(record.model_dump_json(indent=2))
        logger.info(f"Created scan {project_name}/scan{index:02d}")
        return record

    def save_scan_state(self, scan: ScanRecord) -> None:
        scan_dir = PROJECTS_DIR / scan.project_name / f"scan{scan.index:02d}"
        scan_dir.mkdir(parents=True, exist_ok=True)
        (scan_dir / "scan.json").write_text(scan.model_dump_json(indent=2))

    async def add_photo_async(self, scan: ScanRecord, filename: str, data: bytes) -> None:
        scan_dir = PROJECTS_DIR / scan.project_name / f"scan{scan.index:02d}"
        path = scan_dir / filename
        await asyncio.to_thread(path.write_bytes, data)
        if filename not in scan.photos:
            scan.photos.append(filename)

    def get_scan(self, project_name: str, scan_index: int) -> Optional[ScanRecord]:
        scan_json = PROJECTS_DIR / project_name / f"scan{scan_index:02d}" / "scan.json"
        if not scan_json.exists():
            return None
        try:
            return ScanRecord.model_validate_json(scan_json.read_text())
        except Exception as e:
            logger.warning(f"Could not load scan {project_name}/scan{scan_index:02d}: {e}")
            return None

    def get_photo_path(self, project_name: str, scan_index: int, filename: str) -> Optional[Path]:
        # New project-based path
        path = PROJECTS_DIR / project_name / f"scan{scan_index:02d}" / filename
        if path.exists():
            return path
        return None

    def get_legacy_photo_path(self, scan_id: str, filename: str) -> Optional[Path]:
        path = LEGACY_SCANS_DIR / scan_id / filename
        if path.exists():
            return path
        return None

    def delete_scan(self, project_name: str, scan_index: int) -> bool:
        import shutil
        scan_dir = PROJECTS_DIR / project_name / f"scan{scan_index:02d}"
        if scan_dir.exists():
            shutil.rmtree(scan_dir)
            logger.info(f"Deleted {project_name}/scan{scan_index:02d}")
            return True
        return False


def get_project_manager() -> ProjectManager:
    return ProjectManager()
