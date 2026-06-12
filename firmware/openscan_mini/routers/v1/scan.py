"""REST + WebSocket endpoints for scan control."""

import json
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
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
        description="Number of turntable photos per rotor angle (360/steps = angle between shots)"
    )
    settle_ms: int = Field(
        default=300,
        ge=0, le=5000,
        description="Milliseconds to wait after motor move before capture"
    )
    scan_id: Optional[str] = Field(
        default=None,
        description="Custom scan ID (auto-generated if omitted)"
    )
    output_dir: Optional[str] = Field(
        default=None,
        description="Directory to save captured JPEGs (default: ~/openscan-scans)"
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("/")
async def get_scan_status() -> dict:
    """Get current scan state and progress."""
    return _get().get_progress() or {"state": "idle"}


@router.post("/start")
async def start_scan(request: ScanStartRequest) -> dict:
    """
    Start a new scan.

    Moves rotor through each angle in rotor_angles, sweeps turntable
    360° in turntable_steps steps, captures one JPEG per position.
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
        progress = await engine.start(config)
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
# WebSocket — real-time progress
# ------------------------------------------------------------------

@router.websocket("/ws")
async def scan_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time scan progress.

    Sends JSON progress events:
      {"scan_id": "...", "state": "running", "captured": 5, "total": 96,
       "percent": 5.2, "current_rotor": 0.0, "current_turntable": 75.0,
       "elapsed_s": 12.3, "eta_s": 225.1}
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info(f"Scan WebSocket connected ({len(_ws_clients)} clients)")

    # Register broadcast callback on connect
    async def broadcast(data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception:
            pass

    engine = SCAN_ENGINE
    if engine:
        engine.add_broadcast_callback(broadcast)

    try:
        # Send current state immediately on connect
        if engine:
            progress = engine.get_progress()
            if progress:
                await websocket.send_text(json.dumps(progress))

        # Keep connection alive, handle client messages
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


import asyncio  # noqa: E402 — needed for wait_for above
