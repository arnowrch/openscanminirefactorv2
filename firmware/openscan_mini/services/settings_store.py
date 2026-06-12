"""
Generic typed settings store with atomic JSON writes.

Usage:
    store = SettingsStore(CameraSettings, Path("data/settings/camera.json"))
    current = store.load()
    updated = store.update(current, shutter_us=30000)
"""

import logging
from pathlib import Path
from typing import Callable, Generic, Optional, Type, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SettingsStore(Generic[T]):
    def __init__(
        self,
        model_class: Type[T],
        path: Path,
        on_change: Optional[Callable[[T], None]] = None,
    ):
        self._cls = model_class
        self._path = path
        self._on_change = on_change
        path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> T:
        if self._path.exists():
            try:
                return self._cls.model_validate_json(self._path.read_text())
            except Exception as e:
                logger.warning(f"SettingsStore load failed ({self._path}): {e} — using defaults")
        return self._cls()

    def save(self, instance: T) -> None:
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(instance.model_dump_json(indent=2))
            tmp.replace(self._path)
        except Exception as e:
            logger.error(f"SettingsStore save failed ({self._path}): {e}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def update(self, instance: T, **kwargs) -> T:
        data = instance.model_dump()
        data.update({k: v for k, v in kwargs.items() if v is not None})
        new_instance = self._cls(**data)
        self.save(new_instance)
        if self._on_change:
            try:
                self._on_change(new_instance)
            except Exception as e:
                logger.warning(f"SettingsStore on_change callback failed: {e}")
        return new_instance
