"""Error categories, yt-dlp stderr classification and user-facing messages.

Users get a short friendly message plus a job reference. Full details go to
logs only. yt-dlp's wording changes between releases, so every pattern lives
in STDERR_PATTERNS; unmatched errors fall back to INTERNAL.
"""
from __future__ import annotations

import math
import re
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings


class ErrorCategory(str, Enum):
    INVALID_URL = "INVALID_URL"
    BLOCKED_URL = "BLOCKED_URL"
    UNSUPPORTED = "UNSUPPORTED"
    PRIVATE = "PRIVATE"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    GEO_BLOCKED = "GEO_BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    PLATFORM_RATE_LIMIT = "PLATFORM_RATE_LIMIT"
    BOT_CHECK = "BOT_CHECK"
    DRM = "DRM"
    LIVE = "LIVE"
    PLAYLIST = "PLAYLIST"
    TOO_LARGE = "TOO_LARGE"
    TOO_LONG = "TOO_LONG"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    DISK_FULL = "DISK_FULL"
    BUSY = "BUSY"
    USER_LIMIT = "USER_LIMIT"
    DUPLICATE = "DUPLICATE"
    PAUSED = "PAUSED"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    FFMPEG_MISSING = "FFMPEG_MISSING"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    CANCELLED = "CANCELLED"
    INTERNAL = "INTERNAL"


C = ErrorCategory


class BotError(Exception):
    def __init__(self, category: ErrorCategory, detail: str = "", *, retry: float | None = None):
        super().__init__(f"{category.value}: {detail}" if detail else category.value)
        self.category = category
        self.detail = detail
        self.retry = retry


RESTART_NOTICE = "♻️ The bot restarted, please send the link again."

# Order matters: first match wins.
STDERR_PATTERNS: list[tuple[re.Pattern[str], ErrorCategory]] = [
    (re.compile(r"sign in to confirm you.?re not a bot", re.I), C.BOT_CHECK),
    (re.compile(r"rate.?limit reached or login required", re.I), C.LOGIN_REQUIRED),
    (re.compile(r"http error 429|too many requests", re.I), C.PLATFORM_RATE_LIMIT),
    (re.compile(r"unsupported url|no suitable extractor|is not a valid url", re.I), C.UNSUPPORTED),
    (re.compile(r"\bdrm\b", re.I), C.DRM),
    (re.compile(r"private video|video is private|account is private|this content isn.?t available to everyone", re.I), C.PRIVATE),
    (re.compile(r"login required|log in to|sign in to|requires authentication|use --cookies|cookies-from-browser|age.?restricted|confirm your age", re.I), C.LOGIN_REQUIRED),
    (re.compile(r"not available in your country|geo.?restrict|not available from your location", re.I), C.GEO_BLOCKED),
    (re.compile(r"is live|live event will begin|premieres in|is_upcoming", re.I), C.LIVE),
    (re.compile(r"larger than max-filesize|file is larger than", re.I), C.TOO_LARGE),
    (re.compile(r"video unavailable|has been removed|no longer available|does not exist|http error 404|http error 410|not found|no video formats found|no video could be found|requested format is not available", re.I), C.UNAVAILABLE),
    (re.compile(r"timed out|connection reset|temporary failure in name resolution|name or service not known|network is unreachable|unable to download webpage|connection refused|ssl", re.I), C.NETWORK),
]


def classify_ytdlp_error(stderr: str, had_cookies: bool = False) -> ErrorCategory:
    text = stderr or ""
    for pattern, category in STDERR_PATTERNS:
        if pattern.search(text):
            if had_cookies and category in (C.LOGIN_REQUIRED, C.PRIVATE, C.BOT_CHECK):
                return C.AUTH_EXPIRED
            return category
    return C.INTERNAL


_SAME_URL = "That link doesn't look like a public media URL."
_BUSY = "The bot is busy right now, try again in a few minutes."
_PRIVATE = "That content is private or needs a login."
_RATE = "The site is limiting requests, try again later."
_PROC = "Couldn't process the file."

USER_MESSAGES: dict[ErrorCategory, str] = {
    C.INVALID_URL: _SAME_URL,
    C.BLOCKED_URL: _SAME_URL,
    C.UNSUPPORTED: "I can't download from that site.",
    C.PRIVATE: _PRIVATE,
    C.LOGIN_REQUIRED: _PRIVATE,
    C.AUTH_EXPIRED: "Temporarily unavailable for this site (admin alerted).",
    C.GEO_BLOCKED: "Not available from the bot's region.",
    C.UNAVAILABLE: "That post was removed or the link expired.",
    C.PLATFORM_RATE_LIMIT: _RATE,
    C.BOT_CHECK: _RATE,
    C.DRM: "That content is DRM-protected, not supported.",
    C.LIVE: "Live streams aren't supported.",
    C.PLAYLIST: "Playlists and multi-item posts aren't supported, send a link to a single video.",
    C.TOO_LARGE: "That file is over the {max_mb} MB limit.",
    C.TOO_LONG: "That video is over the {max_min} min limit.",
    C.TIMEOUT: "That took too long, try again.",
    C.NETWORK: "Network issue, try again.",
    C.DISK_FULL: _BUSY,
    C.BUSY: _BUSY,
    C.USER_LIMIT: "You've hit your limit{retry}. Wait for your current download or /cancel it.",
    C.DUPLICATE: "Already working on that link.",
    C.PAUSED: "Downloads are paused for maintenance, try again later.",
    C.PROCESSING_FAILED: _PROC,
    C.FFMPEG_MISSING: _PROC,
    C.UPLOAD_FAILED: "Couldn't send the file to Telegram.",
    C.CANCELLED: "Cancelled.",
    C.INTERNAL: "Something broke.",
}


def user_message(err: BaseException, settings: "Settings", job_short: str | None = None) -> str:
    if isinstance(err, BotError):
        category, retry = err.category, err.retry
    else:
        category, retry = C.INTERNAL, None
    retry_txt = f", try again in {max(1, math.ceil(retry / 60))} min" if retry else ""
    template = USER_MESSAGES.get(category, USER_MESSAGES[C.INTERNAL])
    text = template.format(
        max_mb=settings.max_file_mb,
        max_min=max(1, settings.max_duration_sec // 60),
        retry=retry_txt,
    )
    prefix = "✖️ " if category == C.CANCELLED else "❌ "
    if job_short and category not in (C.DUPLICATE, C.CANCELLED):
        text += f" (ref: {job_short})"
    return prefix + text
