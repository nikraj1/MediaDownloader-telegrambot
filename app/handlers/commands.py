"""/start /help /cancel /settings and admin /stats /pause /resume /cookies."""
from __future__ import annotations

import time

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import cookies
from ..config import MB
from ..context import AppContext
from ..errors import BotError, ErrorCategory as C, user_message
from ..policy import STANDARD_HEIGHTS
from .links import group_allowed


def help_text(ctx: AppContext) -> str:
    s = ctx.settings
    limits = (f"up to {s.max_file_mb} MB, {max(1, s.max_duration_sec // 60)} min, "
              f"{s.max_height}p. No live streams" + ("" if s.allow_playlists else ", no playlists"))
    group = {"command": "In groups use /dl <link>.",
             "links": "In groups just post a link or use /dl <link>.",
             "off": "Groups are disabled."}[s.group_mode]
    return (
        "Send me a link to a video (YouTube, Instagram, X, Facebook, TikTok, Reddit and many "
        "other sites) and I'll send the file back.\n\n"
        f"Limits: {limits}.\n{group}\n\n"
        "Commands:\n"
        "/dl <link>: download video\n"
        "/audio <link>: audio only\n"
        "/cancel: stop your current download\n"
        "/settings: auto mode or pick quality each time\n\n"
        "Not every site works every time: sites change and some block servers. "
        "Files are deleted from the bot right after delivery."
    )


def settings_view(ctx: AppContext, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    prefs = ctx.user_settings.get(user_id)
    text = (f"⚙️ Settings\nMode: {prefs['mode']} "
            f"({'download right away' if prefs['mode'] == 'auto' else 'pick quality each time'})\n"
            f"Max quality: {prefs['height']}p")
    mode_row = [
        InlineKeyboardButton(("✅ " if prefs["mode"] == "auto" else "") + "Auto",
                             callback_data="s:mode:auto"),
        InlineKeyboardButton(("✅ " if prefs["mode"] == "ask" else "") + "Ask",
                             callback_data="s:mode:ask"),
    ]
    heights = [h for h in STANDARD_HEIGHTS if h <= ctx.settings.max_height]
    q_row = [InlineKeyboardButton(("✅ " if prefs["height"] == h else "") + f"{h}p",
                                  callback_data=f"s:q:{h}") for h in heights]
    rows = [mode_row] + [q_row[i:i + 3] for i in range(0, len(q_row), 3)]
    return text, InlineKeyboardMarkup(rows)


def _uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}d {h}h {m}m"


def register(client: Client, ctx: AppContext) -> None:
    s = ctx.settings

    def allowed(message: Message) -> bool:
        user = message.from_user
        return (user is not None and ctx.access(user.id) != "blocked"
                and group_allowed(ctx, message))

    def admin_only(message: Message) -> bool:
        return message.from_user is not None and ctx.is_admin(message.from_user.id)

    async def start(_c: Client, message: Message) -> None:
        if not allowed(message):
            return
        await message.reply_text(
            "👋 Hi! Just send me a link to a video and I'll send it back.\n"
            "Type /help for limits and commands.")

    async def help_cmd(_c: Client, message: Message) -> None:
        if allowed(message):
            await message.reply_text(help_text(ctx), disable_web_page_preview=True)

    async def cancel(_c: Client, message: Message) -> None:
        if not allowed(message) or ctx.manager is None:
            return
        job = ctx.manager.latest_for_user(message.from_user.id)
        if job is None:
            await message.reply_text("Nothing to cancel.")
            return
        started = job.task is not None
        ctx.manager.cancel(job)
        if not started and job.status is not None:
            await job.status.set(user_message(BotError(C.CANCELLED), s), force=True, markup=None)

    async def settings_cmd(_c: Client, message: Message) -> None:
        if not allowed(message):
            return
        text, markup = settings_view(ctx, message.from_user.id)
        await message.reply_text(text, reply_markup=markup)

    async def stats(_c: Client, message: Message) -> None:
        if not admin_only(message) or ctx.manager is None:
            return
        m = ctx.manager
        c = m.counters
        await message.reply_text(
            "📊 Stats\n"
            f"Running: {m.running_count()} / {s.max_concurrent_jobs}\n"
            f"Queued: {m.queue.qsize()} / {s.max_queue}\n"
            f"Today: {m.daily_count} / {s.daily_job_cap}\n"
            f"Done: {c['done']}  Failed: {c['failed']}  Cancelled: {c['cancelled']}\n"
            f"Disk free: {ctx.storage.free_bytes() // MB} MB\n"
            f"yt-dlp: {ctx.ytdlp_version}\n"
            f"Paused: {'yes' if m.paused else 'no'}\n"
            f"Uptime: {_uptime(time.time() - ctx.started_at)}")

    async def pause(_c: Client, message: Message) -> None:
        if admin_only(message) and ctx.manager is not None:
            ctx.manager.paused = True
            await message.reply_text("⏸ Paused: new jobs are rejected (running ones continue).")

    async def resume(_c: Client, message: Message) -> None:
        if admin_only(message) and ctx.manager is not None:
            ctx.manager.paused = False
            await message.reply_text("▶️ Resumed.")

    async def cookies_cmd(_c: Client, message: Message) -> None:
        if not admin_only(message):
            return
        found = cookies.status(s.cookies_dir)
        if not found:
            await message.reply_text("🍪 No cookie files configured.")
            return
        lines = "\n".join(f"{name}: modified {mtime}" for name, mtime in found)
        await message.reply_text(f"🍪 Cookie files\n{lines}")

    for fn, names in (
        (start, ["start"]), (help_cmd, ["help"]), (cancel, ["cancel"]),
        (settings_cmd, ["settings"]), (stats, ["stats"]), (pause, ["pause"]),
        (resume, ["resume"]), (cookies_cmd, ["cookies"]),
    ):
        client.add_handler(MessageHandler(fn, filters.command(names)))
