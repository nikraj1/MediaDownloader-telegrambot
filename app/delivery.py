"""Sending files to Telegram and the throttled status message."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified, RPCError

from .config import Settings
from .errors import BotError, ErrorCategory as C
from .media import MediaInfo

log = logging.getLogger(__name__)

CAPTION_MAX = 1024  # Telegram bot caption limit (UTF-16 units)
_KEEP = object()


def _u16len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def build_caption(title: str | None, url: str) -> str:
    title = " ".join((title or "").split())
    src = url if len(url) <= 300 else url[:299] + "…"
    if not title:
        return src[:CAPTION_MAX]
    caption = f"{title}\n\n{src}"
    if _u16len(caption) <= CAPTION_MAX:
        return caption
    budget = CAPTION_MAX - _u16len(src) - 3  # "\n\n" + "…"
    while title and _u16len(title) > budget:
        title = title[:-1]
    return f"{title}…\n\n{src}"


class StatusMessage:
    """One status message per job, edited at most every `min_interval` s."""

    def __init__(self, message: Any, markup: Any = None, min_interval: float = 4.0) -> None:
        self.message = message
        self.markup = markup
        self.min_interval = min_interval
        self._text: str | None = None
        self._last = 0.0
        self._blocked_until = 0.0
        self._label: str | None = None
        self._bucket = -1

    async def set(self, text: str, force: bool = False, markup: Any = _KEEP) -> None:
        markup_changed = False
        if markup is not _KEEP and markup is not self.markup:
            self.markup = markup
            markup_changed = True
        now = time.monotonic()
        if text == self._text and not markup_changed:
            return
        if not force and not markup_changed:
            if now - self._last < self.min_interval or now < self._blocked_until:
                return
        self._last = now
        self._text = text
        await self._edit(text, retry=force)

    async def _edit(self, text: str, retry: bool) -> None:
        try:
            await self.message.edit_text(text, reply_markup=self.markup)
        except MessageNotModified:
            pass
        except FloodWait as exc:
            wait = int(getattr(exc, "value", 5) or 5)
            if retry and wait <= 10:
                await asyncio.sleep(wait)
                try:
                    await self.message.edit_text(text, reply_markup=self.markup)
                except RPCError:
                    pass
            else:
                self._blocked_until = time.monotonic() + wait
        except RPCError as exc:
            log.debug("status edit failed", extra={"err": type(exc).__name__})

    async def progress(self, label: str, pct: float) -> None:
        if label != self._label:
            self._label, self._bucket = label, -1
        bucket = int(max(0.0, min(pct, 100.0)) // 5) * 5
        if bucket == self._bucket:
            return
        self._bucket = bucket
        await self.set(f"{label} {bucket}%")

    async def delete(self) -> None:
        try:
            await self.message.delete()
        except RPCError:
            pass


def choose_kind(path: Path, info: MediaInfo, mode: str) -> str:
    if mode == "audio" or (not info.has_video and info.has_audio):
        return "audio"
    if path.suffix.lower() == ".mp4" and info.vcodec in ("h264", "avc1"):
        return "video"
    return "document"


async def _send(client: Client, kind: str, chat_id: int, path: Path, info: MediaInfo,
                thumb: Path | None, caption: str, title: str | None, performer: str | None,
                progress: Callable[..., Awaitable[None]] | None, reply_markup: Any) -> Any:
    common = dict(caption=caption, parse_mode=ParseMode.DISABLED, progress=progress,
                  reply_markup=reply_markup)
    thumb_arg = str(thumb) if thumb else None
    if kind == "video":
        return await client.send_video(
            chat_id, video=str(path), duration=info.duration, width=info.width,
            height=info.height, thumb=thumb_arg, supports_streaming=True, **common)
    if kind == "audio":
        return await client.send_audio(
            chat_id, audio=str(path), duration=info.duration, title=title,
            performer=performer, thumb=thumb_arg, **common)
    return await client.send_document(chat_id, document=str(path), thumb=thumb_arg, **common)


async def deliver(client: Client, s: Settings, chat_id: int, path: Path, info: MediaInfo,
                  thumb: Path | None, caption: str, mode: str, *, title: str | None = None,
                  performer: str | None = None,
                  progress: Callable[..., Awaitable[None]] | None = None,
                  reply_markup: Any = None) -> Any:
    kind = choose_kind(path, info, mode)
    attempts = [kind] if kind == "document" else [kind, "document"]
    last: Exception | None = None
    for current in attempts:
        for attempt in range(4):
            try:
                return await asyncio.wait_for(
                    _send(client, current, chat_id, path, info, thumb, caption, title,
                          performer, progress, reply_markup if current == kind else None),
                    s.upload_timeout_sec,
                )
            except FloodWait as exc:
                last = exc
                if attempt == 3:
                    break
                await asyncio.sleep(int(getattr(exc, "value", 5) or 5) + 1)
            except TimeoutError:
                raise BotError(C.TIMEOUT, "upload timed out") from None
            except RPCError as exc:
                last = exc
                log.warning("send failed", extra={"kind": current, "err": type(exc).__name__})
                break  # fall through to document
    raise BotError(C.UPLOAD_FAILED, repr(last))
