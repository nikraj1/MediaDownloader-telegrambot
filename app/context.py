"""Shared application state passed to handlers and the pipeline."""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyrogram.errors import RPCError

if TYPE_CHECKING:
    from pyrogram import Client

    from .config import Settings
    from .jobs import JobManager
    from .storage import Storage, UserSettingsStore

log = logging.getLogger(__name__)


class TokenStore:
    """Short tokens for inline buttons (callback data max 64 bytes)."""

    def __init__(self, ttl: float = 600.0) -> None:
        self.ttl = ttl
        self._data: dict[str, tuple[float, dict[str, Any]]] = {}

    def put(self, payload: dict[str, Any]) -> str:
        self._gc()
        token = secrets.token_hex(4)
        self._data[token] = (time.monotonic() + self.ttl, payload)
        return token

    def get(self, token: str) -> dict[str, Any] | None:
        item = self._data.get(token)
        if item is None:
            return None
        if item[0] < time.monotonic():
            self._data.pop(token, None)
            return None
        return item[1]

    def _gc(self) -> None:
        now = time.monotonic()
        for token in [t for t, (exp, _) in self._data.items() if exp < now]:
            del self._data[token]


@dataclass
class AppContext:
    settings: "Settings"
    client: "Client"
    storage: "Storage"
    user_settings: "UserSettingsStore"
    tokens: TokenStore
    ytdlp_version: str = "unknown"
    manager: "JobManager | None" = None
    started_at: float = field(default_factory=time.time)
    _alerts: dict[str, float] = field(default_factory=dict)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.settings.admin_user_ids

    def access(self, user_id: int) -> str:
        """'ok', 'blocked' (ignore silently) or 'denied' (private beta)."""
        s = self.settings
        if user_id in s.blocked_user_ids:
            return "blocked"
        if s.allowed_user_ids and user_id not in s.allowed_user_ids and not self.is_admin(user_id):
            return "denied"
        return "ok"

    async def alert_admins(self, text: str, key: str | None = None, every: float = 3600) -> None:
        key = key or text
        now = time.monotonic()
        if now - self._alerts.get(key, -every - 1) < every:
            return
        self._alerts[key] = now
        for admin in self.settings.admin_user_ids:
            try:
                await self.client.send_message(admin, text)
            except RPCError as exc:
                log.warning("admin alert failed", extra={"err": type(exc).__name__})
