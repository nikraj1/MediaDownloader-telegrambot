"""Download policy: size / duration / live / playlist / DRM rules and the
yt-dlp format arguments for each mode."""
from __future__ import annotations

from typing import Any

from .config import Settings
from .errors import BotError, ErrorCategory as C

STANDARD_HEIGHTS = (360, 480, 720, 1080, 1440, 2160)


def format_args(mode: str, height: int) -> list[str]:
    if mode == "audio":
        # Copies AAC when possible; only re-encodes when the source is e.g. opus.
        return ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a"]
    return [
        "-f", "bv*+ba/b",
        "-S", f"res:{height},vcodec:h264,acodec:aac,ext:mp4:m4a",
        "--merge-output-format", "mp4",
    ]


def check_info(info: dict[str, Any], s: Settings) -> dict[str, Any]:
    """Validate probe output. Returns the single item to download."""
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (info.get("entries") or []) if e]
        if len(entries) == 1 or (s.allow_playlists and entries):
            info = entries[0]
        else:
            raise BotError(C.PLAYLIST, f"{len(entries)} entries")

    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise BotError(C.LIVE, str(info.get("live_status")))

    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > s.max_duration_sec:
        raise BotError(C.TOO_LONG, f"duration {duration}")

    formats = info.get("formats") or []
    if info.get("_has_drm") or (formats and all(f.get("has_drm") for f in formats)):
        raise BotError(C.DRM)

    size = estimate_size(info)
    if size and size > s.max_file_bytes:
        raise BotError(C.TOO_LARGE, f"estimated {size} bytes")
    return info


def estimate_size(info: dict[str, Any]) -> int | None:
    chosen = info.get("requested_formats") or [info]
    total = 0
    for fmt in chosen:
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if not size:
            return None
        total += int(size)
    return total or None


def available_heights(info: dict[str, Any], max_height: int) -> list[int]:
    heights = {
        int(f["height"]) for f in info.get("formats") or []
        if f.get("height") and f.get("vcodec") not in (None, "none")
    }
    if not heights:
        return []
    options = [h for h in STANDARD_HEIGHTS if h <= max_height and any(x >= h * 0.9 for x in heights)]
    if not options:
        options = [min(max_height, max(heights))]
    return options
