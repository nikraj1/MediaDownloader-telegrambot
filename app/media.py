"""FFmpeg / ffprobe helpers. Only ever fed local files from the job dir,
with -protocol_whitelist file so they can't fetch remote URLs."""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .downloader import run_proc, ytdlp_version
from .errors import BotError, ErrorCategory as C

log = logging.getLogger(__name__)

THUMB_MAX_BYTES = 200 * 1024


@dataclass
class MediaInfo:
    duration: int = 0
    width: int = 0
    height: int = 0
    vcodec: str | None = None
    acodec: str | None = None

    @property
    def has_video(self) -> bool:
        return self.vcodec is not None

    @property
    def has_audio(self) -> bool:
        return self.acodec is not None


async def check_tools() -> str:
    """Fail fast at startup. Returns the yt-dlp version."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise BotError(C.FFMPEG_MISSING, f"{tool} not found on PATH")
        res = await run_proc([tool, "-version"], 20, use_nice=False)
        if res.returncode != 0:
            raise BotError(C.FFMPEG_MISSING, f"{tool} -version failed")
    if shutil.which("yt-dlp") is None:
        raise BotError(C.INTERNAL, "yt-dlp not found on PATH")
    return await ytdlp_version()


def _rotation(stream: dict) -> int:
    rotate = (stream.get("tags") or {}).get("rotate")
    if rotate:
        try:
            return int(rotate)
        except ValueError:
            pass
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(side["rotation"])
            except (TypeError, ValueError):
                pass
    return 0


async def probe_media(path: Path, s: Settings) -> MediaInfo:
    res = await run_proc(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        s.process_timeout_sec,
    )
    if res.returncode != 0:
        raise BotError(C.PROCESSING_FAILED, "ffprobe failed: " + res.stderr[-500:])
    data = json.loads(res.stdout or "{}")
    info = MediaInfo()
    try:
        info.duration = int(float((data.get("format") or {}).get("duration") or 0))
    except ValueError:
        pass
    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        if kind == "video" and info.vcodec is None:
            if (stream.get("disposition") or {}).get("attached_pic"):
                continue
            info.vcodec = stream.get("codec_name")
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            if abs(_rotation(stream)) in (90, 270):
                info.width, info.height = info.height, info.width
        elif kind == "audio" and info.acodec is None:
            info.acodec = stream.get("codec_name")
    return info


def is_faststart(path: Path) -> bool:
    """True if the MP4 'moov' box comes before 'mdat'."""
    try:
        with open(path, "rb") as fh:
            pos = 0
            for _ in range(64):
                fh.seek(pos)
                header = fh.read(8)
                if len(header) < 8:
                    return False
                size = int.from_bytes(header[:4], "big")
                box = header[4:8]
                if size == 1:
                    size = int.from_bytes(fh.read(8), "big")
                elif size == 0:
                    return False
                if box == b"moov":
                    return True
                if box == b"mdat":
                    return False
                if size < 8:
                    return False
                pos += size
    except OSError:
        return False
    return False


async def ensure_faststart(path: Path, info: MediaInfo, s: Settings) -> Path:
    if path.suffix.lower() != ".mp4" or not info.has_video or is_faststart(path):
        return path
    out = path.with_name(path.stem + ".faststart.mp4")
    res = await run_proc(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file",
         "-i", str(path), "-map", "0", "-c", "copy", "-movflags", "+faststart",
         "-threads", "2", str(out)],
        s.process_timeout_sec,
    )
    if res.returncode == 0 and out.is_file():
        os.replace(out, path)
    else:
        log.warning("faststart remux failed, sending original")
        out.unlink(missing_ok=True)
    return path


async def make_thumbnail(path: Path, info: MediaInfo, job_dir: Path, s: Settings) -> Path | None:
    if not info.has_video:
        return None
    thumb = job_dir / "thumb.jpg"
    ts = min(1.0, info.duration / 2) if info.duration else 0.0
    for quality in ("4", "12"):
        res = await run_proc(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file",
             "-ss", f"{ts:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
             "-q:v", quality, str(thumb)],
            s.process_timeout_sec,
        )
        if res.returncode != 0 or not thumb.is_file():
            return None
        if thumb.stat().st_size <= THUMB_MAX_BYTES:
            return thumb
    thumb.unlink(missing_ok=True)
    return None
