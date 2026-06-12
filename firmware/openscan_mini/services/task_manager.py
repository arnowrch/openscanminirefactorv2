"""
Task management system — ported from OpenScan3 architecture.

Provides BaseTask (ABC), TaskManager (singleton), and all status/progress models.
Supports exclusive tasks (motors+camera), pause/resume, JSON persistence, and
startup recovery of interrupted tasks.
"""

import asyncio
import inspect
import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TASKS_DIR = Path("data/tasks")
PROGRESS_PERSIST_INTERVAL = 2.0  # seconds between progress saves


# ── Status & Progress Models ────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"
    INTERRUPTED = "interrupted"


class TaskProgress(BaseModel):
    current: float = 0.0
    total: float = 0.0
    message: str = ""

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(100.0 * self.current / self.total, 1)


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    is_exclusive: bool = False
    status: TaskStatus = TaskStatus.PENDING
    progress: TaskProgress = Field(default_factory=TaskProgress)
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    run_kwargs: dict = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


# ── BaseTask ABC ─────────────────────────────────────────────────────────────

class BaseTask(ABC):
    """
    Abstract base for all background tasks.

    Subclasses implement `run()` as either:
    - A plain async coroutine returning a result
    - An async generator yielding TaskProgress objects

    Exclusive tasks (is_exclusive = True) block all other exclusive tasks.
    """

    is_exclusive: bool = False

    def __init__(self, task_model: Task):
        self._task_model = task_model
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # starts in "not paused" state

    @property
    def id(self) -> str:
        return self._task_model.id

    @property
    def name(self) -> str:
        return self._task_model.name

    @property
    def task_model(self) -> Task:
        return self._task_model

    @abstractmethod
    async def run(self) -> Any:
        """Override with task logic. May be a coroutine or async generator."""
        raise NotImplementedError

    def cancel(self) -> None:
        """Signal cancellation. Also unblocks any active pause."""
        self._stop_event.set()
        self._pause_event.set()

    def is_cancelled(self) -> bool:
        return self._stop_event.is_set()

    def pause(self) -> None:
        """Signal task to pause at its next checkpoint."""
        self._pause_event.clear()

    def resume(self) -> None:
        """Resume a paused task."""
        self._pause_event.set()

    async def wait_for_pause(self) -> None:
        """
        Await this at safe checkpoints inside run().
        Blocks while paused; returns immediately otherwise.
        """
        await self._pause_event.wait()


# ── TaskManager Singleton ────────────────────────────────────────────────────

class TaskManager:
    _instance: Optional["TaskManager"] = None

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._tasks: dict[str, Task] = {}
        self._instances: dict[str, BaseTask] = {}
        self._exclusive_running: bool = False
        self._queue: list[BaseTask] = []  # simple FIFO list
        self._queue_lock = asyncio.Lock()
        self._last_progress_persist: dict[str, float] = {}

        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"TaskManager initialized — tasks dir: {TASKS_DIR}")

    # ── Public API ──────────────────────────────────────────────────────────

    async def create_and_run_task(self, task_instance: BaseTask) -> Task:
        """
        Register and start (or queue) a task.
        Returns the Task model immediately with status PENDING or RUNNING.
        """
        task = task_instance.task_model
        self._tasks[task.id] = task
        self._instances[task.id] = task_instance
        await self._persist(task)

        if self._can_start(task_instance):
            asyncio.create_task(self._run_wrapper(task_instance))
        else:
            logger.info(f"Task {task.name}({task.id[:8]}) queued — exclusive running")
            self._queue.append(task_instance)

        return task

    async def pause_task(self, task_id: str) -> Optional[Task]:
        task = self._tasks.get(task_id)
        instance = self._instances.get(task_id)
        if not task or not instance:
            return None
        if task.status != TaskStatus.RUNNING:
            return task
        task.status = TaskStatus.PAUSED
        instance.pause()
        await self._persist(task)
        logger.info(f"Task {task.name}({task_id[:8]}) paused")
        return task

    async def resume_task(self, task_id: str) -> Optional[Task]:
        task = self._tasks.get(task_id)
        instance = self._instances.get(task_id)
        if not task or not instance:
            return None
        if task.status != TaskStatus.PAUSED:
            return task
        task.status = TaskStatus.RUNNING
        instance.resume()
        await self._persist(task)
        logger.info(f"Task {task.name}({task_id[:8]}) resumed")
        return task

    async def cancel_task(self, task_id: str) -> Optional[Task]:
        task = self._tasks.get(task_id)
        instance = self._instances.get(task_id)
        if not task:
            return None

        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR):
            return task

        # Remove from queue if pending
        if task.status == TaskStatus.PENDING and instance in self._queue:
            self._queue.remove(instance)
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now(timezone.utc)
            await self._persist(task)
            return task

        # Signal running task
        if instance:
            instance.cancel()
        task.status = TaskStatus.CANCELLED
        await self._persist(task)
        logger.info(f"Task {task.name}({task_id[:8]}) cancellation signalled")
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    async def restore_interrupted_tasks(self) -> None:
        """
        Called at startup. Any RUNNING tasks from a previous session are
        marked INTERRUPTED so users can see what was in-progress.
        """
        if not TASKS_DIR.exists():
            return
        for json_file in TASKS_DIR.glob("*.json"):
            try:
                data = json.loads(json_file.read_text())
                task = Task.model_validate(data)
                if task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED):
                    task.status = TaskStatus.INTERRUPTED
                    json_file.write_text(task.model_dump_json(indent=2))
                    logger.info(f"Recovered interrupted task: {task.name}({task.id[:8]})")
                self._tasks[task.id] = task
            except Exception as e:
                logger.warning(f"Could not restore task from {json_file}: {e}")

    # ── Internal ─────────────────────────────────────────────────────────────

    def _can_start(self, instance: BaseTask) -> bool:
        if instance.is_exclusive and self._exclusive_running:
            return False
        if not instance.is_exclusive and self._exclusive_running:
            return False
        return True

    async def _run_wrapper(self, instance: BaseTask) -> None:
        task = instance.task_model
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)
        if instance.is_exclusive:
            self._exclusive_running = True
        await self._persist(task)
        logger.info(f"Task {task.name}({task.id[:8]}) started")

        try:
            result = instance.run()

            if inspect.isasyncgen(result):
                # Async generator — iterate and collect TaskProgress yields
                async for progress in result:
                    if isinstance(progress, TaskProgress):
                        task.progress = progress
                        await self._maybe_persist_progress(task)
                # Generator exhausted without cancellation → completed
                if task.status not in (TaskStatus.CANCELLED, TaskStatus.ERROR):
                    task.status = TaskStatus.COMPLETED
            else:
                # Plain coroutine
                task.result = await result
                if task.status not in (TaskStatus.CANCELLED, TaskStatus.ERROR):
                    task.status = TaskStatus.COMPLETED

        except asyncio.CancelledError:
            if task.status not in (TaskStatus.CANCELLED,):
                task.status = TaskStatus.CANCELLED
        except Exception as e:
            task.status = TaskStatus.ERROR
            task.error = f"{type(e).__name__}: {e}"
            logger.error(f"Task {task.name}({task.id[:8]}) failed: {e}", exc_info=True)
        finally:
            task.completed_at = datetime.now(timezone.utc)
            task.progress.current = task.progress.total  # mark 100%
            if instance.is_exclusive:
                self._exclusive_running = False
            self._instances.pop(task.id, None)
            await self._persist(task)
            logger.info(f"Task {task.name}({task.id[:8]}) → {task.status}")
            # Process any queued tasks
            asyncio.create_task(self._process_queue())

    async def _process_queue(self) -> None:
        async with self._queue_lock:
            while self._queue:
                next_instance = self._queue[0]
                if self._can_start(next_instance):
                    self._queue.pop(0)
                    asyncio.create_task(self._run_wrapper(next_instance))
                    if next_instance.is_exclusive:
                        break  # exclusive blocks rest of queue
                else:
                    break

    async def _persist(self, task: Task) -> None:
        try:
            path = TASKS_DIR / f"{task.id}.json"
            path.write_text(task.model_dump_json(indent=2))
        except Exception as e:
            logger.warning(f"Could not persist task {task.id[:8]}: {e}")

    async def _maybe_persist_progress(self, task: Task) -> None:
        import time
        now = time.monotonic()
        last = self._last_progress_persist.get(task.id, 0.0)
        if now - last >= PROGRESS_PERSIST_INTERVAL:
            self._last_progress_persist[task.id] = now
            await self._persist(task)


# ── Module-level singleton accessor ─────────────────────────────────────────

_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
