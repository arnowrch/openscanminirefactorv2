"""REST + WebSocket endpoints for scan control and scan library."""

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openscan_mini.models.scan import ScanSetting
from openscan_mini.services.project_manager import ProjectManager, get_project_manager
from openscan_mini.services.task_manager import Task, TaskStatus, get_task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scan", tags=["scan"])

# Hardware references — injected from main.py
_MOTORS = None
_CAMERA = None
_RINGLIGHT = None
_SCAN_ENGINE = None  # kept for backward compat (legacy start path)

# Track the most recently created scan task id for pause/resume/stop
_active_task_id: Optional[str] = None

_ws_clients: set = set()


# ── Dependency injection ─────────────────────────────────────────────────────

def set_scan_engine(engine) -> None:
    """Legacy: still injected from main.py so old code doesn't break."""
    global _SCAN_ENGINE
    _SCAN_ENGINE = engine


def set_hardware(motors, camera, ringlight) -> None:
    global _MOTORS, _CAMERA, _RINGLIGHT
    _MOTORS = motors
    _CAMERA = camera
    _RINGLIGHT = ringlight


# ── Legacy flat-scan helper ───────────────────────────────────────────────────

def _legacy_root() -> Path:
    return Path.home() / "openscan-scans"


# ── Request model (backward compat + new fields) ─────────────────────────────

class ScanStartRequest(BaseModel):
    """
    Accepts both legacy fields (rotor_angles + turntable_steps) and the new
    ScanSetting fields. Legacy fields are converted to ScanSetting automatically.
    """
    # New fields
    path_method: str = "sweep"
    points: int = Field(default=130, ge=1, le=999)
    min_theta: float = 12.0
    max_theta: float = 125.0
    min_phi: float = 0.0
    max_phi: float = 360.0
    optimize_path: bool = True
    focus_stacks: int = 1
    focus_range: tuple[float, float] = (8.0, 14.0)
    project_name: str = "default"
    # Legacy / shared fields
    rotor_angles: list[float] = Field(default=[-20.0, 0.0, 20.0, 40.0])
    turntable_steps: int = Field(default=24, ge=4, le=200)
    settle_ms: int = Field(default=300, ge=0, le=5000)
    pause_before_capture_ms: int = 0
    scan_id: Optional[str] = None


def _to_scan_setting(req: ScanStartRequest) -> ScanSetting:
    return ScanSetting(
        path_method=req.path_method,
        rotor_angles=req.rotor_angles,
        turntable_steps=req.turntable_steps,
        points=req.points,
        min_theta=req.min_theta,
        max_theta=req.max_theta,
        min_phi=req.min_phi,
        max_phi=req.max_phi,
        optimize_path=req.optimize_path,
        focus_stacks=req.focus_stacks,
        focus_range=req.focus_range,
        pause_before_capture_ms=req.pause_before_capture_ms,
        settle_ms=req.settle_ms,
        project_name=req.project_name,
        scan_id=req.scan_id,
    )


# ── Scan status ───────────────────────────────────────────────────────────────

@router.get("/")
async def get_scan_status() -> dict:
    """Get current scan state and progress."""
    if _active_task_id:
        task = get_task_manager().get_task(_active_task_id)
        if task:
            pm = get_project_manager()
            scan_index = task.run_kwargs.get("scan_index")
            project_name = task.run_kwargs.get("project_name", "default")
            return {
                "state": task.status,
                "task_id": task.id,
                "project": project_name,
                "scan_index": scan_index,
                "current": int(task.progress.current),
                "total": int(task.progress.total),
                "percent": task.progress.percent,
                "message": task.progress.message,
                "error": task.error,
            }

    # Fallback: legacy ScanEngine
    if _SCAN_ENGINE:
        progress = _SCAN_ENGINE.get_progress()
        if progress:
            return progress

    return {"state": "idle"}


# ── Start / Stop / Pause / Resume ─────────────────────────────────────────────

@router.post("/start")
async def start_scan(request: ScanStartRequest) -> dict:
    """
    Start a new scan using the task system.

    Accepts both legacy (rotor_angles + turntable_steps) and new
    (path_method=fibonacci, points=130, ...) parameters.
    """
    global _active_task_id

    # Check if a scan is already running
    if _active_task_id:
        existing = get_task_manager().get_task(_active_task_id)
        if existing and existing.status in (TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.PENDING):
            raise HTTPException(status_code=409, detail="A scan is already running")

    if not _MOTORS or not _CAMERA:
        raise HTTPException(status_code=503, detail="Hardware not ready")

    settings = _to_scan_setting(request)

    from openscan_mini.services.scan_task import ScanTask
    task_model = Task(name="scan_task", is_exclusive=True)
    instance = ScanTask(
        task_model=task_model,
        settings=settings,
        motors=_MOTORS,
        camera=_CAMERA,
        ringlight=_RINGLIGHT,
        project_manager=get_project_manager(),
    )

    tm = get_task_manager()
    task = await tm.create_and_run_task(instance)
    _active_task_id = task.id

    return {
        "status": "started",
        "task_id": task.id,
        "project": settings.project_name,
        "path_method": settings.path_method,
        "scan_id": settings.scan_id,
    }


@router.post("/stop")
async def stop_scan() -> dict:
    """Cancel the active scan."""
    global _active_task_id
    if not _active_task_id:
        # Legacy fallback
        if _SCAN_ENGINE:
            _SCAN_ENGINE.stop()
            return {"status": "cancel_requested"}
        return {"status": "idle", "message": "No active scan"}

    task = await get_task_manager().cancel_task(_active_task_id)
    if not task:
        return {"status": "not_found"}
    return {"status": "cancel_requested", "task_id": task.id}


@router.post("/pause")
async def pause_scan() -> dict:
    """Pause the active scan at the next safe checkpoint."""
    if not _active_task_id:
        raise HTTPException(status_code=404, detail="No active scan")
    task = await get_task_manager().pause_task(_active_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scan task not found")
    return {"status": task.status, "task_id": task.id}


@router.post("/resume")
async def resume_scan() -> dict:
    """Resume a paused scan."""
    if not _active_task_id:
        raise HTTPException(status_code=404, detail="No active scan")
    task = await get_task_manager().resume_task(_active_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scan task not found")
    return {"status": task.status, "task_id": task.id}


# ── Scan library ──────────────────────────────────────────────────────────────

@router.get("/library")
async def list_scans() -> dict:
    """
    List all projects and their scans.
    Includes legacy flat scans from ~/openscan-scans/ under 'legacy' project.
    """
    pm = get_project_manager()
    projects = await asyncio.to_thread(pm.list_projects)
    return {"projects": projects, "count": len(projects)}


@router.get("/library/{project_or_scan_id}")
async def get_scan_or_project(project_or_scan_id: str) -> dict:
    """
    Dual-mode endpoint for backward compat:
    - If it looks like a legacy scan ID (~/openscan-scans/{id} exists): return files
    - Otherwise: return project info
    """
    if ".." in project_or_scan_id:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Try legacy flat scan first (backward compat)
    legacy_dir = _legacy_root() / project_or_scan_id
    if legacy_dir.exists() and legacy_dir.is_dir():
        photos = sorted(legacy_dir.glob("*.jpg"))
        return {
            "scan_id": project_or_scan_id,
            "project": "legacy",
            "photo_count": len(photos),
            "files": [
                {
                    "filename": p.name,
                    "size_bytes": p.stat().st_size,
                    "download_url": f"/api/v1/scan/library/{project_or_scan_id}/{p.name}",
                }
                for p in photos
            ],
        }

    # Try as project name
    pm = get_project_manager()
    projects = await asyncio.to_thread(pm.list_projects)
    for proj in projects:
        if proj["name"] == project_or_scan_id:
            return proj

    raise HTTPException(status_code=404, detail=f"'{project_or_scan_id}' not found")


@router.get("/library/{project_name}/{scan_index}")
async def get_project_scan(project_name: str, scan_index: int) -> dict:
    """List photos in a project scan."""
    if ".." in project_name:
        raise HTTPException(status_code=400, detail="Invalid path")

    pm = get_project_manager()
    scan = await asyncio.to_thread(pm.get_scan, project_name, scan_index)
    if not scan:
        raise HTTPException(
            status_code=404,
            detail=f"Scan {project_name}/scan{scan_index:02d} not found",
        )

    from openscan_mini.services.project_manager import PROJECTS_DIR
    scan_dir = PROJECTS_DIR / project_name / f"scan{scan_index:02d}"
    photos = sorted(scan_dir.glob("*.jpg"))

    return {
        "project": project_name,
        "scan_index": scan_index,
        "scan_id": scan.scan_id,
        "status": scan.status,
        "photo_count": len(photos),
        "current_step": scan.current_step,
        "total_steps": scan.total_steps,
        "task_id": scan.task_id,
        "focus_task_id": scan.focus_task_id,
        "files": [
            {
                "filename": p.name,
                "size_bytes": p.stat().st_size,
                "download_url": f"/api/v1/scan/library/{project_name}/{scan_index}/{p.name}",
            }
            for p in photos
        ],
    }


@router.get("/library/{project_name}/{scan_index}/{filename}")
async def download_project_photo(project_name: str, scan_index: int, filename: str):
    """Download a photo from a project scan."""
    if ".." in project_name or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")

    pm = get_project_manager()
    path = await asyncio.to_thread(pm.get_photo_path, project_name, scan_index, filename)
    if not path:
        raise HTTPException(status_code=404, detail=f"Photo '{filename}' not found")

    return FileResponse(path=str(path), media_type="image/jpeg", filename=filename)


@router.get("/library/{scan_id}/{filename}")
async def download_legacy_photo(scan_id: str, filename: str):
    """Download a photo from a legacy flat scan (backward compat)."""
    if ".." in scan_id or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Try legacy path
    pm = get_project_manager()
    path = await asyncio.to_thread(pm.get_legacy_photo_path, scan_id, filename)
    if path:
        return FileResponse(path=str(path), media_type="image/jpeg", filename=filename)

    raise HTTPException(status_code=404, detail=f"File '{filename}' not found in scan '{scan_id}'")


@router.delete("/library/{scan_id}")
async def delete_scan(scan_id: str) -> dict:
    """Delete a legacy flat scan (backward compat)."""
    if ".." in scan_id:
        raise HTTPException(status_code=400, detail="Invalid scan ID")
    scan_dir = _legacy_root() / scan_id
    if not scan_dir.exists():
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found")
    count = len(list(scan_dir.glob("*.jpg")))
    shutil.rmtree(scan_dir)
    return {"status": "deleted", "scan_id": scan_id, "photos_deleted": count}


@router.delete("/library/{project_name}/{scan_index}")
async def delete_project_scan(project_name: str, scan_index: int) -> dict:
    """Delete a project scan."""
    if ".." in project_name:
        raise HTTPException(status_code=400, detail="Invalid path")
    pm = get_project_manager()
    ok = await asyncio.to_thread(pm.delete_scan, project_name, scan_index)
    if not ok:
        raise HTTPException(status_code=404, detail="Scan not found")
    return {"status": "deleted", "project": project_name, "scan_index": scan_index}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def scan_websocket(websocket: WebSocket):
    """
    Real-time scan progress via WebSocket.

    Sends: {state, task_id, project, scan_index, current, total, percent, message}
    Ping/pong: client sends "ping", server responds {type: "pong"}
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info(f"Scan WebSocket connected ({len(_ws_clients)} clients)")

    # Legacy engine callback for old-style scans
    async def legacy_broadcast(data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception:
            pass

    if _SCAN_ENGINE:
        _SCAN_ENGINE.add_broadcast_callback(legacy_broadcast)

    try:
        # Send current state immediately on connect
        status = await get_scan_status()
        await websocket.send_text(json.dumps(status))

        _last_sent_state: Optional[str] = None

        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                if msg == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Push current scan progress every 0.5s when active
                if _active_task_id:
                    task = get_task_manager().get_task(_active_task_id)
                    if task and task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED):
                        payload = {
                            "state": task.status,
                            "task_id": task.id,
                            "project": task.run_kwargs.get("project_name", ""),
                            "scan_index": task.run_kwargs.get("scan_index"),
                            "current": int(task.progress.current),
                            "total": int(task.progress.total),
                            "percent": task.progress.percent,
                            "message": task.progress.message,
                        }
                        try:
                            await websocket.send_text(json.dumps(payload))
                        except Exception:
                            break
                        _last_sent_state = task.status
                    elif task and task.status in (
                        TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR
                    ):
                        # Send terminal state only once — prevents toast spam on error
                        if _last_sent_state != task.status:
                            try:
                                await websocket.send_text(json.dumps({
                                    "state": task.status,
                                    "task_id": task.id,
                                    "error": task.error,
                                }))
                            except Exception:
                                pass
                            _last_sent_state = task.status

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Scan WebSocket closed: {e}")
    finally:
        _ws_clients.discard(websocket)
        if _SCAN_ENGINE:
            _SCAN_ENGINE.remove_broadcast_callback(legacy_broadcast)
        logger.info(f"Scan WebSocket disconnected ({len(_ws_clients)} clients)")
