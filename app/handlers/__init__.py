"""Telegram handlers. Only this package and delivery.py touch the client API."""
from __future__ import annotations

from pyrogram import Client

from ..context import AppContext
from . import callbacks, commands, links


def register_all(client: Client, ctx: AppContext) -> None:
    commands.register(client, ctx)
    links.register(client, ctx)
    callbacks.register(client, ctx)
