"""REST API endpoints for task management."""

import logging

from fastapi import APIRouter, HTTPException

from openscan_mini.services.task_manager import Task, TaskStatus, get_task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("")
async def list_tasks() -> dict:
    """List all tasks (running, queued, completed, failed)."""
    tm = get_task_manager()
    tasks = tm.list_tasks()
    return {
        "tasks": [_task_dict(t) for t in tasks],
        "count": len(tasks),
        "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
    }


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    """Get a single task by ID."""
    task = get_task_manager().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return _task_dict(task)


@router.post("/{task_id}/pause")
async def pause_task(task_id: str) -> dict:
    """Pause a running task."""
    task = await get_task_manager().pause_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return _task_dict(task)


@router.post("/{task_id}/resume")
async def resume_task(task_id: str) -> dict:
    """Resume a paused task."""
    task = await get_task_manager().resume_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return _task_dict(task)


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """Cancel a running or queued task."""
    task = await get_task_manager().cancel_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return _task_dict(task)


def _task_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status,
        "progress": {
            "current": task.progress.current,
            "total": task.progress.total,
            "percent": task.progress.percent,
            "message": task.progress.message,
        },
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "is_exclusive": task.is_exclusive,
    }
