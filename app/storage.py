"""Job directories, disk checks, janitor, inflight.json and user settings."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from .config import MB, Settings

log = logging.getLogger(__name__)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        log.warning("unreadable state file, resetting", extra={"file": path.name})
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.job_root = settings.job_dir
        self.state_dir = settings.state_dir
        self._inflight_path = self.state_dir / "inflight.json"

    # --- lifecycle -------------------------------------------------------
    def prepare(self) -> None:
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.wipe_jobs()

    def wipe_jobs(self) -> None:
        for child in self.job_root.iterdir():
            self.remove_path(child)

    @staticmethod
    def remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except OSError:
                pass

    def create_job_dir(self, job_id: str) -> Path:
        path = self.job_root / job_id
        path.mkdir(mode=0o700)
        return path

    def remove_job_dir(self, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    # --- disk ------------------------------------------------------------
    def free_bytes(self) -> int:
        return shutil.disk_usage(self.job_root).free

    def free_ratio(self) -> float:
        usage = shutil.disk_usage(self.job_root)
        return usage.free / usage.total if usage.total else 1.0

    def disk_ok(self) -> bool:
        need = (self.s.min_free_disk_mb + self.s.max_file_mb * 2) * MB
        return self.free_bytes() >= need

    def sweep(self, active_ids: Iterable[str]) -> int:
        """Delete stale job dirs not owned by an active job."""
        active = set(active_ids)
        now = time.time()
        removed = 0
        candidates = []
        for child in self.job_root.iterdir():
            if child.name in active:
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            if now - mtime > self.s.job_ttl_sec:
                self.remove_path(child)
                removed += 1
            else:
                candidates.append((mtime, child))
        # Over the total budget: drop the oldest inactive dirs first.
        if dir_size(self.job_root) > self.s.max_job_dir_mb * MB:
            log.warning("job dir over budget, pruning")
            for _mtime, child in sorted(candidates):
                self.remove_path(child)
                removed += 1
                if dir_size(self.job_root) <= self.s.max_job_dir_mb * MB:
                    break
        return removed

    # --- inflight --------------------------------------------------------
    def inflight_add(self, job_id: str, chat_id: int, message_id: int) -> None:
        data = _read_json(self._inflight_path, {})
        data[job_id] = {"chat_id": chat_id, "message_id": message_id}
        _write_json_atomic(self._inflight_path, data)

    def inflight_remove(self, job_id: str) -> None:
        data = _read_json(self._inflight_path, {})
        if data.pop(job_id, None) is not None:
            _write_json_atomic(self._inflight_path, data)

    def inflight_take_all(self) -> list[dict[str, int]]:
        data = _read_json(self._inflight_path, {})
        _write_json_atomic(self._inflight_path, {})
        return list(data.values())


async def janitor_loop(
    storage: Storage,
    active_ids: Callable[[], Iterable[str]],
    alert: Callable[[str], Awaitable[None]] | None = None,
    interval: int = 600,
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            removed = storage.sweep(active_ids())
            if removed:
                log.info("janitor removed stale job dirs", extra={"count": removed})
            if alert and storage.free_ratio() < 0.15:
                await alert(f"⚠️ Disk low: {storage.free_bytes() // MB} MB free on the job volume.")
        except Exception:
            log.exception("janitor failed")


class UserSettingsStore:
    """Tiny per-user prefs: mode (auto|ask) and preferred max height."""

    def __init__(self, state_dir: Path, default_height: int) -> None:
        self._path = state_dir / "settings.json"
        self._default_height = default_height
        self._data: dict[str, dict[str, Any]] = _read_json(self._path, {})

    def get(self, user_id: int) -> dict[str, Any]:
        prefs = self._data.get(str(user_id), {})
        return {
            "mode": prefs.get("mode", "auto"),
            "height": min(int(prefs.get("height", self._default_height)), self._default_height),
        }

    def set(self, user_id: int, **values: Any) -> None:
        prefs = self._data.setdefault(str(user_id), {})
        prefs.update(values)
        try:
            _write_json_atomic(self._path, self._data)
        except OSError:
            log.warning("could not persist user settings")
