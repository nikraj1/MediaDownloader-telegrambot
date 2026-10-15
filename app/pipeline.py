"""The per-job pipeline: guard -> probe -> policy -> download -> process ->
upload -> cleanup. Runs inside a JobManager worker."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import cookies, downloader, media, policy
from .context import AppContext
from .delivery import build_caption, deliver
from .errors import RESTART_NOTICE, BotError, ErrorCategory as C, user_message
from .jobs import Job, JobState

log = logging.getLogger(__name__)


def cancel_markup(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"c:{job_id}")]])


class Pipeline:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.s = ctx.settings

    async def run(self, job: Job) -> None:
        status = job.status
        assert status is not None
        job_dir: Path | None = None
        extra = {"job": job.short, "user": job.user_id, "mode": job.mode}
        try:
            if not self.ctx.storage.disk_ok():
                raise BotError(C.DISK_FULL, "free disk below threshold")
            job_dir = self.ctx.storage.create_job_dir(job.id)
            cookie = cookies.prepare(job.url, self.s.cookies_dir, job_dir)

            job.state = JobState.PROBING
            await status.set("🔎 Checking link…", force=True)
            log.info("probe", extra=extra)
            info = await downloader.probe(job.url, self.s, job_dir, cookie, job.mode, job.height)
            info = policy.check_info(info, self.s)

            if job.ask:
                await self._ask(job, info)
                return

            job.state = JobState.DOWNLOADING
            await status.set("📥 Downloading…", force=True)

            async def dl_progress(pct: float) -> None:
                await status.progress("📥 Downloading", pct)

            path = await downloader.download(job.url, self.s, job_dir, cookie, job.mode,
                                             job.height, on_progress=dl_progress)

            job.state = JobState.PROCESSING
            await status.set("⚙️ Processing…", force=True)
            minfo = await media.probe_media(path, self.s)
            path = await media.ensure_faststart(path, minfo, self.s)
            thumb = await media.make_thumbnail(path, minfo, job_dir, self.s)

            job.state = JobState.UPLOADING
            await status.set("📤 Uploading…", force=True)

            async def up_progress(current: int, total: int) -> None:
                if total:
                    await status.progress("📤 Uploading", current * 100 / total)

            markup = None
            if job.mode == "video":
                token = self.ctx.tokens.put({"url": job.url, "user_id": job.user_id})
                markup = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🎵 Audio only", callback_data=f"a:{token}")]])
            await deliver(
                self.ctx.client, self.s, job.chat_id, path, minfo, thumb,
                build_caption(info.get("title"), job.url), job.mode,
                title=info.get("track") or info.get("title"),
                performer=info.get("artist") or info.get("uploader"),
                progress=up_progress, reply_markup=markup,
            )
            job.state = JobState.DONE
            log.info("done", extra={**extra, "bytes": path.stat().st_size})
            await status.delete()
        except asyncio.CancelledError:
            job.state = JobState.CANCELLED
            text = RESTART_NOTICE if job.shutdown else user_message(BotError(C.CANCELLED), self.s)
            await status.set(text, force=True, markup=None)
            log.info("cancelled", extra={**extra, "shutdown": job.shutdown})
            raise
        except BotError as exc:
            job.state = JobState.FAILED
            log.warning("job failed", extra={**extra, "category": exc.category.value,
                                              "detail": exc.detail[-1500:]})
            if exc.category == C.AUTH_EXPIRED:
                await self.ctx.alert_admins(
                    "⚠️ Cookie login failed for a site, refresh its cookie file "
                    f"(job {job.short}).", key="auth_expired")
            await status.set(user_message(exc, self.s, job.short), force=True, markup=None)
        except Exception:
            job.state = JobState.FAILED
            log.exception("job crashed", extra=extra)
            await status.set(user_message(BotError(C.INTERNAL), self.s, job.short),
                             force=True, markup=None)
        finally:
            if job_dir is not None:
                self.ctx.storage.remove_job_dir(job_dir)

    async def _ask(self, job: Job, info: dict) -> None:
        heights = policy.available_heights(info, self.s.max_height)
        token = self.ctx.tokens.put({"url": job.url, "user_id": job.user_id})
        row = [InlineKeyboardButton(f"{h}p", callback_data=f"q:{token}:{h}") for h in heights]
        rows = [row[i:i + 3] for i in range(0, len(row), 3)]
        rows.append([
            InlineKeyboardButton("🎵 Audio", callback_data=f"q:{token}:a"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"q:{token}:x"),
        ])
        job.state = JobState.DONE
        title = (info.get("title") or "").strip()[:200]
        text = f"Pick a quality{': ' + title if title else ''}"
        await job.status.set(text, force=True, markup=InlineKeyboardMarkup(rows))
