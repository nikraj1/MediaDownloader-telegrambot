"""Text messages containing URLs -> submit a job."""
from __future__ import annotations

import logging
from typing import Any

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.errors import RPCError
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from .. import netguard
from ..context import AppContext
from ..delivery import StatusMessage
from ..errors import BotError, user_message
from ..jobs import Job
from ..pipeline import cancel_markup
from ..urls import first_url, normalise_url

log = logging.getLogger(__name__)


def group_allowed(ctx: AppContext, message: Message) -> bool:
    if message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        return ctx.settings.group_mode != "off"
    return True


async def enqueue(ctx: AppContext, job: Job, *, reply_to: Message | None = None,
                  status_msg: Any = None) -> None:
    markup = cancel_markup(job.id)
    try:
        if status_msg is None:
            assert reply_to is not None
            status_msg = await reply_to.reply_text("⏳ Queued…", reply_markup=markup)
        else:
            await status_msg.edit_text("⏳ Queued…", reply_markup=markup)
    except RPCError as exc:
        log.warning("could not create status message", extra={"err": type(exc).__name__})
        return
    job.status = StatusMessage(status_msg, markup)
    assert ctx.manager is not None
    try:
        ahead = ctx.manager.submit(job, is_admin=ctx.is_admin(job.user_id))
    except BotError as exc:
        await job.status.set(user_message(exc, ctx.settings), force=True, markup=None)
        return
    if ahead:
        await job.status.set(f"⏳ Queued (position {ahead})…", force=True)


async def handle_link_message(ctx: AppContext, message: Message, mode: str = "video") -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    access = ctx.access(user.id)
    if access == "blocked":
        return
    if access == "denied":
        await message.reply_text("🔒 This bot is in private beta.")
        return
    raw = first_url(message.text or message.caption, message.entities or message.caption_entities)
    if not raw:
        if message.chat.type == ChatType.PRIVATE or (message.text or "").startswith("/"):
            await message.reply_text("Send me a link to a video, e.g. /dl https://…")
        return
    try:
        url = normalise_url(raw)
        await netguard.check_url(url)
    except BotError as exc:
        log.info("url rejected", extra={"user": user.id, "category": exc.category.value,
                                        "detail": exc.detail})
        await message.reply_text(user_message(exc, ctx.settings))
        return
    prefs = ctx.user_settings.get(user.id)
    job = Job(user_id=user.id, chat_id=message.chat.id, url=url, mode=mode,
              height=prefs["height"], ask=(prefs["mode"] == "ask" and mode == "video"))
    await enqueue(ctx, job, reply_to=message)


def register(client: Client, ctx: AppContext) -> None:
    not_command = ~filters.regex(r"^/")

    async def on_private(_client: Client, message: Message) -> None:
        await handle_link_message(ctx, message)

    async def on_group(_client: Client, message: Message) -> None:
        if ctx.settings.group_mode == "links":
            await handle_link_message(ctx, message)

    async def on_dl(_client: Client, message: Message) -> None:
        if group_allowed(ctx, message):
            await handle_link_message(ctx, message)

    async def on_audio(_client: Client, message: Message) -> None:
        if group_allowed(ctx, message):
            await handle_link_message(ctx, message, mode="audio")

    client.add_handler(MessageHandler(on_dl, filters.command(["dl", "download"])))
    client.add_handler(MessageHandler(on_audio, filters.command(["audio", "mp3"])))
    client.add_handler(MessageHandler(on_private, filters.private & filters.text & not_command))
    client.add_handler(MessageHandler(on_group, filters.group & filters.text & not_command))
