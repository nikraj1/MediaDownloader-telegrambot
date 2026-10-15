"""yt-dlp as a subprocess: probe + download, argument building, progress.

Arguments are always a list (never a shell) and the URL always follows "--"
so a URL starting with "-" can't become an option. Each child runs in its own
session/process group so cancel and timeouts kill yt-dlp *and* its FFmpeg
children.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shutil
import signal
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import policy
from .config import Settings
from .errors import BotError, ErrorCategory as C, classify_ytdlp_error

log = logging.getLogger(__name__)

STREAM_LIMIT = 64 * 1024 * 1024  # probe JSON can be one very long line
SECRET_ENV = frozenset({"API_ID", "API_HASH", "BOT_TOKEN"})
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PROGRESS = re.compile(r"PROGRESS\s+([\d.]+)%")

LineCallback = Callable[[str], Awaitable[None] | None]
ProgressCallback = Callable[[float], Awaitable[None]]


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in SECRET_ENV}


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), 5)
            return
        except TimeoutError:
            continue


async def run_proc(
    argv: list[str],
    timeout: float,
    *,
    cwd: Path | None = None,
    on_line: LineCallback | None = None,
    use_nice: bool = True,
) -> ProcResult:
    if use_nice and shutil.which("nice"):
        argv = ["nice", "-n", "10", *argv]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=child_env(),
        start_new_session=True,
        limit=STREAM_LIMIT,
    )
    stdout_lines: list[str] = []
    stderr_tail: deque[str] = deque(maxlen=200)

    async def pump(stream: asyncio.StreamReader, sink: list[str] | deque[str]) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            sink.append(line)
            if on_line is not None:
                result = on_line(line)
                if inspect.isawaitable(result):
                    await result

    try:
        await asyncio.wait_for(
            asyncio.gather(pump(proc.stdout, stdout_lines), pump(proc.stderr, stderr_tail), proc.wait()),
            timeout,
        )
    except TimeoutError:
        await _terminate(proc)
        raise BotError(C.TIMEOUT, f"{Path(argv[0]).name} exceeded {timeout}s") from None
    finally:
        if proc.returncode is None:
            await _terminate(proc)
    return ProcResult(proc.returncode or 0, "\n".join(stdout_lines), "\n".join(stderr_tail))


def _common_args(s: Settings, job_dir: Path, cookie: Path | None) -> list[str]:
    args = [
        "yt-dlp", "--ignore-config", "--no-warnings",
        "--socket-timeout", "20", "--retries", "3", "--extractor-retries", "2",
        "--cache-dir", str(job_dir / ".cache"),
    ]
    args += ["--playlist-items", "1"] if s.allow_playlists else ["--no-playlist"]
    if not s.allow_generic_extractor:
        args += ["--use-extractors", "default,-generic"]
    if cookie is not None:
        args += ["--cookies", str(cookie)]
    return args


async def probe(url: str, s: Settings, job_dir: Path, cookie: Path | None,
                mode: str, height: int) -> dict[str, Any]:
    argv = _common_args(s, job_dir, cookie) + policy.format_args(mode, height) + [
        "--dump-single-json", "--", url,
    ]
    res = await run_proc(argv, s.probe_timeout_sec, cwd=job_dir)
    if res.returncode != 0:
        raise BotError(classify_ytdlp_error(res.stderr, cookie is not None), res.stderr[-2000:])
    try:
        return json.loads(res.stdout)
    except ValueError:
        raise BotError(C.INTERNAL, "probe returned invalid JSON") from None


async def download(url: str, s: Settings, job_dir: Path, cookie: Path | None, mode: str,
                   height: int, on_progress: ProgressCallback | None = None) -> Path:
    argv = _common_args(s, job_dir, cookie) + policy.format_args(mode, height) + [
        "--playlist-items", "1",
        "--max-filesize", f"{s.max_file_mb}M",
        "--concurrent-fragments", "2",
        "--no-mtime",
        "--restrict-filenames",
        "-P", str(job_dir),
        "-o", "%(id).80s.%(ext)s",
        "--hls-prefer-native",
        # VERIFY syntax on your pinned yt-dlp version.
        "--postprocessor-args", "Merger+ffmpeg_o:-movflags +faststart",
        "--no-simulate",
        "--print", "after_move:FILEPATH:%(filepath)s",
        "--progress", "--newline",
        "--progress-template", "download:PROGRESS %(progress._percent_str)s",
        "--", url,
    ]
    final: list[str] = []

    async def on_line(line: str) -> None:
        if line.startswith("FILEPATH:"):
            final.append(line[len("FILEPATH:"):].strip())
            return
        if on_progress is not None and "PROGRESS" in line:
            match = _PROGRESS.search(_ANSI.sub("", line))
            if match:
                try:
                    await on_progress(float(match.group(1)))
                except ValueError:
                    pass

    res = await run_proc(argv, s.download_timeout_sec, cwd=job_dir, on_line=on_line)
    if res.returncode != 0:
        raise BotError(classify_ytdlp_error(res.stderr, cookie is not None), res.stderr[-2000:])
    if not final:
        cat = classify_ytdlp_error(res.stderr + "\n" + res.stdout, cookie is not None)
        raise BotError(cat, "yt-dlp produced no output file: " + res.stderr[-1000:])

    path = Path(final[-1]).resolve()
    root = job_dir.resolve()
    if root not in path.parents:
        raise BotError(C.INTERNAL, "output path escaped the job dir")
    if not path.is_file():
        raise BotError(C.INTERNAL, "output file missing")
    if path.stat().st_size > s.max_file_bytes:
        raise BotError(C.TOO_LARGE, f"{path.stat().st_size} bytes")
    return path


async def ytdlp_version() -> str:
    res = await run_proc(["yt-dlp", "--version"], 20, use_nice=False)
    return res.stdout.strip() or "unknown"
