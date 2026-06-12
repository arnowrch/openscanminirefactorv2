"""REST + WebSocket endpoints for scan control and scan library."""

import asyncio
import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openscan_mini.controllers.scan import ScanConfig, ScanEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scan", tags=["scan"])

SCAN_ENGINE: Optional[ScanEngine] = None
_ws_clients: set = set()


def set_scan_engine(engine: ScanEngine) -> None:
    global SCAN_ENGINE
    SCAN_ENGINE = engine


def _get() -> ScanEngine:
    if not SCAN_ENGINE:
        raise HTTPException(status_code=500, detail="Scan engine not initialized")
    return SCAN_ENGINE


def _scans_root() -> Path:
    """Return the scan library root, consistent with ScanConfig default."""
    return Path.home() / "openscan-scans"


# ------------------------------------------------------------------
# REST models
# ------------------------------------------------------------------

class ScanStartRequest(BaseModel):
    rotor_angles: List[float] = Field(
        default=[-20.0, 0.0, 20.0, 40.0],
        description="Rotor positions in degrees (e.g. [-20, 0, 20, 40])"
    )
    turntable_steps: int = Field(
        default=24,
        ge=4, le=200,
        description="Number of turntable photos per rotor angle (360/steps = degrees between shots)"
    )
    settle_ms: int = Field(
        default=300,
        ge=0, le=5000,
        description="Milliseconds to wait after motor move before capture"
    )
    scan_id: Optional[str] = Field(
        default=None,
        description="Custom scan ID (auto-generated as timestamp if omitted)"
    )
    output_dir: Optional[str] = Field(
        default=None,
        description="Directory to save JPEGs (default: ~/openscan-scans)"
    )


# ------------------------------------------------------------------
# Current scan status
# ------------------------------------------------------------------

@router.get("/")
async def get_scan_status() -> dict:
    """Get current scan state and progress."""
    engine = SCAN_ENGINE
    if not engine:
        return {"state": "idle"}
    return engine.get_progress() or {"state": "idle"}


@router.post("/start")
async def start_scan(request: ScanStartRequest) -> dict:
    """
    Start a new scan.

    Moves rotor through each angle in rotor_angles, sweeps turntable
    360° in turntable_steps steps, captures one JPEG per position.
    Ringlight turns on at start and off at end automatically.
    """
    engine = _get()

    config = ScanConfig(
        rotor_angles=request.rotor_angles,
        turntable_steps=request.turntable_steps,
        settle_ms=request.settle_ms,
    )
    if request.output_dir:
        config.output_dir = request.output_dir
    if request.scan_id:
        config.scan_id = request.scan_id

    try:
        await engine.start(config)
        return {
            "status": "started",
            "scan_id": config.scan_id,
            "total_photos": config.total_photos,
            "rotor_angles": config.rotor_angles,
            "turntable_steps": config.turntable_steps,
            "output_dir": config.output_dir,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/stop")
async def stop_scan() -> dict:
    """Cancel the running scan after the current photo."""
    _get().stop()
    return {"status": "cancel_requested"}


# ------------------------------------------------------------------
# Scan library — list, browse, download
# ------------------------------------------------------------------

@router.get("/library")
async def list_scans() -> dict:
    """
    List all completed scans in ~/openscan-scans.

    Returns scan IDs, photo counts, and total size per scan.
    """
    root = _scans_root()
    if not root.exists():
        return {"scans": [], "root": str(root)}

    scans = []
    for scan_dir in sorted(root.iterdir(), reverse=True):
        if not scan_dir.is_dir():
            continue
        photos = sorted(scan_dir.glob("*.jpg"))
        total_bytes = sum(p.stat().st_size for p in photos)
        scans.append({
            "scan_id": scan_dir.name,
            "photo_count": len(photos),
            "size_mb": round(total_bytes / 1024 / 1024, 1),
            "path": str(scan_dir),
        })

    return {
        "scans": scans,
        "total": len(scans),
        "root": str(root),
    }


@router.get("/library/{scan_id}")
async def get_scan_files(scan_id: str) -> dict:
    """
    List all files in a specific scan.

    Returns filenames, sizes, and download URLs.
    """
    scan_dir = _scans_root() / scan_id
    if not scan_dir.exists():
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found")

    photos = sorted(scan_dir.glob("*.jpg"))
    files = [
        {
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "download_url": f"/api/v1/scan/library/{scan_id}/{p.name}",
        }
        for p in photos
    ]

    return {
        "scan_id": scan_id,
        "photo_count": len(files),
        "files": files,
    }


@router.get("/library/{scan_id}/{filename}")
async def download_photo(scan_id: str, filename: str):
    """Download a single photo from a scan."""
    # Security: prevent path traversal
    if ".." in scan_id or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")

    photo_path = _scans_root() / scan_id / filename
    if not photo_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found in scan '{scan_id}'")

    return FileResponse(
        path=str(photo_path),
        media_type="image/jpeg",
        filename=filename,
    )


@router.delete("/library/{scan_id}")
async def delete_scan(scan_id: str) -> dict:
    """Delete all photos in a scan. The scan directory is removed."""
    import shutil
    if ".." in scan_id:
        raise HTTPException(status_code=400, detail="Invalid scan ID")

    scan_dir = _scans_root() / scan_id
    if not scan_dir.exists():
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found")

    photo_count = len(list(scan_dir.glob("*.jpg")))
    shutil.rmtree(scan_dir)
    logger.info(f"Deleted scan '{scan_id}' ({photo_count} photos)")
    return {"status": "deleted", "scan_id": scan_id, "photos_deleted": photo_count}


# ------------------------------------------------------------------
# WebSocket — real-time progress
# ------------------------------------------------------------------

@router.websocket("/ws")
async def scan_websocket(websocket: WebSocket):
    """
    WebSocket for real-time scan progress.

    Events: {"scan_id", "state", "captured", "total", "percent",
             "current_rotor", "current_turntable", "elapsed_s", "eta_s"}
    Client can send "ping" to get a "pong" response.
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info(f"Scan WebSocket connected ({len(_ws_clients)} clients)")

    async def broadcast(data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception:
            pass

    engine = SCAN_ENGINE
    if engine:
        engine.add_broadcast_callback(broadcast)

    try:
        if engine:
            progress = engine.get_progress()
            if progress:
                await websocket.send_text(json.dumps(progress))

        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if msg == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "keepalive"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Scan WebSocket closed: {e}")
    finally:
        _ws_clients.discard(websocket)
        if engine:
            engine.remove_broadcast_callback(broadcast)
        logger.info(f"Scan WebSocket disconnected ({len(_ws_clients)} clients)")
