"""Heartbeat writer + `python -m app.health` checker for Docker HEALTHCHECK."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Callable

HEARTBEAT_FILE = "heartbeat"
MAX_AGE_SEC = 90


async def heartbeat_loop(state_dir: Path, is_healthy: Callable[[], bool] | None = None,
                         interval: float = 15.0) -> None:
    path = state_dir / HEARTBEAT_FILE
    tmp = path.with_suffix(".tmp")
    while True:
        try:
            if is_healthy is None or is_healthy():
                tmp.write_text(str(time.time()))
                os.replace(tmp, path)
        except OSError:
            pass
        await asyncio.sleep(interval)


def check(state_dir: Path, max_age: float = MAX_AGE_SEC) -> bool:
    try:
        beat = float((state_dir / HEARTBEAT_FILE).read_text().strip())
    except (OSError, ValueError):
        return False
    return time.time() - beat <= max_age


def main() -> None:
    state_dir = Path(os.environ.get("STATE_DIR", "/data/state"))
    sys.exit(0 if check(state_dir) else 1)


if __name__ == "__main__":
    main()
