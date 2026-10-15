"""Entry point: config, logging, client, workers, janitor, heartbeat and
graceful shutdown on SIGTERM (docker stop)."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import __version__
from .config import ConfigError, Settings, load_settings
from .errors import RESTART_NOTICE, BotError
from .logging_setup import setup_logging

log = logging.getLogger("app")

SHUTDOWN_GRACE_SEC = 45  # keep below compose stop_grace_period (60 s)


async def _notify_restarted(client, storage) -> None:
    from pyrogram.errors import RPCError

    for entry in storage.inflight_take_all():
        try:
            await client.edit_message_text(entry["chat_id"], entry["message_id"], RESTART_NOTICE)
        except (RPCError, KeyError, TypeError):
            pass


async def run(settings: Settings) -> None:
    from . import media
    from .client import build_client
    from .context import AppContext, TokenStore
    from .handlers import register_all
    from .health import heartbeat_loop
    from .jobs import JobManager
    from .pipeline import Pipeline
    from .storage import Storage, UserSettingsStore, janitor_loop

    log.info("starting", extra={"version": __version__})
    if settings.enable_transcode:
        log.warning("ENABLE_TRANSCODE is not implemented in v1, ignoring")
    if not settings.allowed_user_ids:
        log.warning("ALLOWED_USER_IDS is empty: the bot is PUBLIC")

    ytv = await media.check_tools()
    log.info("tools ok", extra={"yt_dlp": ytv})

    storage = Storage(settings)
    storage.prepare()  # wipes leftover job dirs from a crash
    client = build_client(settings)
    ctx = AppContext(
        settings=settings, client=client, storage=storage,
        user_settings=UserSettingsStore(settings.state_dir, settings.max_height),
        tokens=TokenStore(), ytdlp_version=ytv,
    )
    manager = JobManager(settings, Pipeline(ctx).run, storage)
    ctx.manager = manager
    register_all(client, ctx)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await client.start()
    me = await client.get_me()
    log.info("logged in", extra={"bot": me.username})
    await _notify_restarted(client, storage)
    await manager.start()
    background = [
        asyncio.create_task(janitor_loop(storage, manager.active_ids,
                                         lambda text: ctx.alert_admins(text, key="disk"))),
        asyncio.create_task(heartbeat_loop(settings.state_dir,
                                           lambda: bool(getattr(client, "is_connected", True)))),
    ]

    await stop.wait()
    log.info("shutdown requested")
    queued = await manager.shutdown(SHUTDOWN_GRACE_SEC)
    for job in queued:
        if job.status is not None:
            await job.status.set(RESTART_NOTICE, force=True, markup=None)
        storage.inflight_remove(job.id)
    for task in background:
        task.cancel()
    await asyncio.gather(*background, return_exceptions=True)
    await client.stop()
    log.info("stopped")


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(2)
    setup_logging(settings.log_level, settings.log_format, settings.secrets)
    try:
        asyncio.run(run(settings))
    except BotError as exc:
        log.error("startup failed", extra={"category": exc.category.value, "detail": exc.detail})
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
