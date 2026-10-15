"""Job model and JobManager: bounded queue, N workers, per-user limits,
dedupe, daily cap, cancel and graceful shutdown.

An asyncio queue is the right tool for one process on one VPS with jobs
that are fine to lose on restart. Move to Redis/arq only for multiple
worker processes/hosts or durable jobs.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .config import Settings
from .errors import BotError, ErrorCategory as C
from .ratelimit import SlidingWindowLimiter

if TYPE_CHECKING:
    from .delivery import StatusMessage
    from .storage import Storage

log = logging.getLogger(__name__)


class JobState(str, Enum):
    QUEUED = "QUEUED"
    PROBING = "PROBING"
    AWAITING_CHOICE = "AWAITING_CHOICE"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"
    UPLOADING = "UPLOADING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {JobState.DONE, JobState.FAILED, JobState.CANCELLED}


@dataclass(eq=False)
class Job:
    user_id: int
    chat_id: int
    url: str
    mode: str = "video"  # video | audio
    height: int = 720
    ask: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: JobState = JobState.QUEUED
    status: "StatusMessage | None" = None
    task: "asyncio.Task[Any] | None" = None
    cancelled: bool = False
    shutdown: bool = False
    created: float = field(default_factory=time.time)

    @property
    def short(self) -> str:
        return self.id[:8]

    @property
    def key(self) -> tuple[int, str, str, int]:
        return (self.user_id, self.url, self.mode, self.height)


class JobManager:
    def __init__(self, settings: Settings, runner: Callable[[Job], Awaitable[None]],
                 storage: "Storage | None" = None) -> None:
        self.s = settings
        self._runner = runner
        self._storage = storage
        self.queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=settings.max_queue)
        self.jobs: dict[str, Job] = {}
        self._keys: dict[tuple, Job] = {}
        self.limiter = SlidingWindowLimiter(settings.user_rate_count, settings.user_rate_window)
        self.paused = False
        self.accepting = True
        self.daily_count = 0
        self._day = None
        self.counters: Counter[str] = Counter()
        self._workers: list[asyncio.Task[None]] = []

    # --- submission ------------------------------------------------------
    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self.daily_count = today, 0
            self.limiter.gc()

    def running_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.task is not None and not j.task.done())

    def submit(self, job: Job, *, is_admin: bool = False) -> int:
        """Enqueue or raise BotError. Returns the number of jobs ahead of it."""
        if not self.accepting:
            raise BotError(C.BUSY, "shutting down")
        if self.paused and not is_admin:
            raise BotError(C.PAUSED)
        self._roll_day()
        if self.daily_count >= self.s.daily_job_cap and not is_admin:
            raise BotError(C.BUSY, "daily cap reached")
        if job.key in self._keys:
            raise BotError(C.DUPLICATE)
        if not is_admin:
            active = sum(1 for j in self.jobs.values() if j.user_id == job.user_id)
            if active >= self.s.max_active_per_user:
                raise BotError(C.USER_LIMIT, "active job limit")
            wait = self.limiter.retry_after(job.user_id)
            if wait > 0:
                raise BotError(C.USER_LIMIT, "rate limited", retry=wait)
        ahead = self.queue.qsize() + max(0, self.running_count() - self.s.max_concurrent_jobs + 1)
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            raise BotError(C.BUSY, "queue full") from None
        self.jobs[job.id] = job
        self._keys[job.key] = job
        if not is_admin:
            self.limiter.hit(job.user_id)
        self.daily_count += 1
        self.counters["submitted"] += 1
        if self._storage is not None and job.status is not None:
            self._storage.inflight_add(job.id, job.chat_id, job.status.message.id)
        busy = self.running_count() >= self.s.max_concurrent_jobs
        return ahead if busy else 0

    def _forget(self, job: Job) -> None:
        self.jobs.pop(job.id, None)
        if self._keys.get(job.key) is job:
            del self._keys[job.key]
        if self._storage is not None:
            self._storage.inflight_remove(job.id)

    # --- workers ---------------------------------------------------------
    async def start(self) -> None:
        for n in range(self.s.max_concurrent_jobs):
            self._workers.append(asyncio.create_task(self._worker(n), name=f"worker-{n}"))

    async def _worker(self, n: int) -> None:
        while True:
            job = await self.queue.get()
            try:
                if job.cancelled:
                    continue
                job.task = asyncio.create_task(self._runner(job), name=f"job-{job.short}")
                await asyncio.wait({job.task})
                self._record(job)
            finally:
                self._forget(job)
                self.queue.task_done()

    def _record(self, job: Job) -> None:
        task = job.task
        if task is None:
            return
        if task.cancelled():
            self.counters["cancelled"] += 1
        elif task.exception() is not None:
            self.counters["failed"] += 1
            log.error("job runner crashed", exc_info=task.exception(), extra={"job": job.short})
        else:
            self.counters[job.state.value.lower()] += 1

    # --- control ---------------------------------------------------------
    def latest_for_user(self, user_id: int) -> Job | None:
        mine = [j for j in self.jobs.values() if j.user_id == user_id and not j.cancelled]
        return max(mine, key=lambda j: j.created) if mine else None

    def cancel(self, job: Job) -> bool:
        if job.cancelled or job.state in TERMINAL:
            return False
        job.cancelled = True
        if job.task is not None and not job.task.done():
            job.task.cancel()
        else:
            job.state = JobState.CANCELLED
            self._forget(job)
            self.counters["cancelled"] += 1
        return True

    def active_ids(self) -> set[str]:
        return set(self.jobs)

    async def shutdown(self, grace: float) -> list[Job]:
        """Stop accepting, let running jobs finish up to `grace`, cancel the
        rest. Returns queued jobs that never started (caller notifies)."""
        self.accepting = False
        running = {j.task for j in self.jobs.values() if j.task is not None and not j.task.done()}
        if running:
            _done, pending = await asyncio.wait(running, timeout=grace)
            for job in list(self.jobs.values()):
                if job.task in pending:
                    job.shutdown = True
                    job.task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=15)
        queued = [j for j in self.jobs.values() if j.task is None and not j.cancelled]
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        return queued
