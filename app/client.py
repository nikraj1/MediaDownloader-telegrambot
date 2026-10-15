"""Builds the Kurigram (Pyrogram fork) client.

Kurigram installs the `pyrogram` import name, so handler code is unchanged.
Uninstall the old `pyrogram` package first; both packages ship the same
module and will clash.
"""
from __future__ import annotations

from pyrogram import Client
from pyrogram.enums import ParseMode

from .config import Settings

SESSION_NAME = "mediabot"


def build_client(settings: Settings) -> Client:
    settings.session_dir.mkdir(parents=True, exist_ok=True)
    return Client(
        SESSION_NAME,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        workdir=str(settings.session_dir),
        # User content (titles, descriptions) must never be parsed as markup.
        parse_mode=ParseMode.DISABLED,
        workers=8,
        # Short FloodWaits are slept automatically by the library.
        sleep_threshold=30,
    )
