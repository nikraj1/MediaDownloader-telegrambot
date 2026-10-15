"""Inline button presses: cancel, quality choice, audio-only, settings."""
from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.errors import RPCError
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery

from ..context import AppContext
from ..errors import BotError, ErrorCategory as C, user_message
from ..jobs import Job
from ..policy import STANDARD_HEIGHTS
from .commands import settings_view
from .links import enqueue

log = logging.getLogger(__name__)
EXPIRED = "That button expired, please send the link again."


def register(client: Client, ctx: AppContext) -> None:
    s = ctx.settings

    async def on_callback(_c: Client, cq: CallbackQuery) -> None:
        data = cq.data if isinstance(cq.data, str) else (cq.data or b"").decode()
        user_id = cq.from_user.id
        if ctx.access(user_id) == "blocked":
            await cq.answer()
            return
        kind, _, rest = data.partition(":")
        try:
            if kind == "c":
                await _cancel(cq, user_id, rest)
            elif kind == "q":
                await _quality(cq, user_id, rest)
            elif kind == "a":
                await _audio(cq, user_id, rest)
            elif kind == "s":
                await _settings(cq, user_id, rest)
            else:
                await cq.answer()
        except RPCError as exc:
            log.warning("callback failed", extra={"err": type(exc).__name__})

    def _owner_ok(user_id: int, owner: int) -> bool:
        return user_id == owner or ctx.is_admin(user_id)

    async def _cancel(cq: CallbackQuery, user_id: int, job_id: str) -> None:
        job = ctx.manager.jobs.get(job_id) if ctx.manager else None
        if job is None:
            await cq.answer("Nothing to cancel.")
            return
        if not _owner_ok(user_id, job.user_id):
            await cq.answer("This button isn't for you.")
            return
        started = job.task is not None
        ctx.manager.cancel(job)
        if not started and job.status is not None:
            await job.status.set(user_message(BotError(C.CANCELLED), s), force=True, markup=None)
        await cq.answer("Cancelling…")

    async def _quality(cq: CallbackQuery, user_id: int, rest: str) -> None:
        token, _, choice = rest.partition(":")
        payload = ctx.tokens.get(token)
        if payload is None:
            await cq.answer(EXPIRED, show_alert=True)
            return
        if not _owner_ok(user_id, payload["user_id"]):
            await cq.answer("This button isn't for you.")
            return
        if choice == "x":
            await cq.message.edit_text(user_message(BotError(C.CANCELLED), s))
            await cq.answer()
            return
        if choice == "a":
            job = Job(user_id=payload["user_id"], chat_id=cq.message.chat.id,
                      url=payload["url"], mode="audio", height=s.max_height)
        else:
            try:
                height = min(int(choice), s.max_height)
            except ValueError:
                await cq.answer()
                return
            job = Job(user_id=payload["user_id"], chat_id=cq.message.chat.id,
                      url=payload["url"], mode="video", height=height)
        await cq.answer()
        await enqueue(ctx, job, status_msg=cq.message)

    async def _audio(cq: CallbackQuery, user_id: int, token: str) -> None:
        payload = ctx.tokens.get(token)
        if payload is None:
            await cq.answer(EXPIRED, show_alert=True)
            return
        if not _owner_ok(user_id, payload["user_id"]):
            await cq.answer("This button isn't for you.")
            return
        job = Job(user_id=payload["user_id"], chat_id=cq.message.chat.id,
                  url=payload["url"], mode="audio", height=s.max_height)
        await cq.answer()
        await enqueue(ctx, job, reply_to=cq.message)

    async def _settings(cq: CallbackQuery, user_id: int, rest: str) -> None:
        field_name, _, value = rest.partition(":")
        if field_name == "mode" and value in ("auto", "ask"):
            ctx.user_settings.set(user_id, mode=value)
        elif field_name == "q" and value.isdigit() and int(value) in STANDARD_HEIGHTS:
            ctx.user_settings.set(user_id, height=min(int(value), s.max_height))
        text, markup = settings_view(ctx, user_id)
        try:
            await cq.message.edit_text(text, reply_markup=markup)
        except RPCError:
            pass
        await cq.answer("Saved")

    client.add_handler(CallbackQueryHandler(on_callback))
