"""Environment parsing and validation into a frozen Settings dataclass.

No extra dependencies: python-dotenv is used only if installed (local dev).
Every problem raises ConfigError with a one-line message such as
"API_ID is missing". Secret fields are excluded from repr().
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

GROUP_MODES = ("command", "off", "links")
LOG_FORMATS = ("json", "text")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
MB = 1024 * 1024


class ConfigError(Exception):
    """Raised with a short, human readable message."""


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional in prod
        return
    load_dotenv(override=False)


def _raw(name: str) -> str:
    return os.environ.get(name, "").strip()


def _required(name: str) -> str:
    value = _raw(name)
    if not value or value == "replace_me":
        raise ConfigError(f"{name} is missing")
    return value


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _raw(name)
    if raw == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value < lo or value > hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name).lower()
    if raw == "":
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _ids(name: str) -> frozenset[int]:
    raw = _raw(name)
    out: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            raise ConfigError(
                f"{name} must be comma-separated numeric Telegram IDs, got {part!r}"
            ) from None
    return frozenset(out)


def _choice(name: str, default: str, choices: tuple[str, ...], upper: bool = False) -> str:
    raw = _raw(name) or default
    raw = raw.upper() if upper else raw.lower()
    if raw not in choices:
        raise ConfigError(f"{name} must be one of {', '.join(choices)}, got {raw!r}")
    return raw


def _rate(name: str, default: str) -> tuple[int, int]:
    raw = _raw(name) or default
    try:
        count_s, window_s = raw.split("/", 1)
        count, window = int(count_s), int(window_s)
    except ValueError:
        raise ConfigError(f"{name} must look like '5/600' (jobs/seconds), got {raw!r}") from None
    if not (1 <= count <= 1000 and 1 <= window <= 86400):
        raise ConfigError(f"{name} out of range, got {raw!r}")
    return count, window


def _path(name: str, default: str) -> Path:
    return Path(_raw(name) or default)


@dataclass(frozen=True)
class Settings:
    # Telegram
    api_id: int
    api_hash: str = field(repr=False)
    bot_token: str = field(repr=False)
    # Access control
    admin_user_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    blocked_user_ids: frozenset[int]
    group_mode: str
    # Limits
    max_concurrent_jobs: int
    max_queue: int
    max_active_per_user: int
    user_rate_count: int
    user_rate_window: int
    daily_job_cap: int
    max_file_mb: int
    max_duration_sec: int
    max_height: int
    allow_playlists: bool
    allow_generic_extractor: bool
    enable_transcode: bool
    # Timeouts
    probe_timeout_sec: int
    download_timeout_sec: int
    process_timeout_sec: int
    upload_timeout_sec: int
    # Storage
    job_dir: Path
    state_dir: Path
    session_dir: Path
    cookies_dir: Path
    min_free_disk_mb: int
    max_job_dir_mb: int
    job_ttl_sec: int
    # Logging
    log_level: str
    log_format: str

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.api_hash, self.bot_token)

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * MB


def load_settings() -> Settings:
    _load_dotenv()
    api_id_raw = _required("API_ID")
    try:
        api_id = int(api_id_raw)
    except ValueError:
        raise ConfigError("API_ID must be a number (from my.telegram.org)") from None
    if api_id <= 0:
        raise ConfigError("API_ID must be a positive number")

    rate_count, rate_window = _rate("USER_RATE_LIMIT", "5/600")
    return Settings(
        api_id=api_id,
        api_hash=_required("API_HASH"),
        bot_token=_required("BOT_TOKEN"),
        admin_user_ids=_ids("ADMIN_USER_IDS"),
        allowed_user_ids=_ids("ALLOWED_USER_IDS"),
        blocked_user_ids=_ids("BLOCKED_USER_IDS"),
        group_mode=_choice("GROUP_MODE", "command", GROUP_MODES),
        max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 2, 1, 8),
        max_queue=_int("MAX_QUEUE", 10, 1, 200),
        max_active_per_user=_int("MAX_ACTIVE_PER_USER", 1, 1, 10),
        user_rate_count=rate_count,
        user_rate_window=rate_window,
        daily_job_cap=_int("DAILY_JOB_CAP", 500, 1, 100_000),
        max_file_mb=_int("MAX_FILE_MB", 500, 1, 2000),
        max_duration_sec=_int("MAX_DURATION_SEC", 1800, 10, 6 * 3600),
        max_height=_int("MAX_HEIGHT", 720, 144, 2160),
        allow_playlists=_bool("ALLOW_PLAYLISTS", False),
        allow_generic_extractor=_bool("ALLOW_GENERIC_EXTRACTOR", False),
        enable_transcode=_bool("ENABLE_TRANSCODE", False),
        probe_timeout_sec=_int("PROBE_TIMEOUT_SEC", 60, 5, 600),
        download_timeout_sec=_int("DOWNLOAD_TIMEOUT_SEC", 900, 30, 7200),
        process_timeout_sec=_int("PROCESS_TIMEOUT_SEC", 300, 10, 3600),
        upload_timeout_sec=_int("UPLOAD_TIMEOUT_SEC", 1200, 30, 7200),
        job_dir=_path("JOB_DIR", "/data/jobs"),
        state_dir=_path("STATE_DIR", "/data/state"),
        session_dir=_path("SESSION_DIR", "/data/session"),
        cookies_dir=_path("COOKIES_DIR", "/run/cookies"),
        min_free_disk_mb=_int("MIN_FREE_DISK_MB", 2048, 0, 1_000_000),
        max_job_dir_mb=_int("MAX_JOB_DIR_MB", 3000, 100, 1_000_000),
        job_ttl_sec=_int("JOB_TTL_SEC", 3600, 60, 7 * 86400),
        log_level=_choice("LOG_LEVEL", "INFO", LOG_LEVELS, upper=True),
        log_format=_choice("LOG_FORMAT", "json", LOG_FORMATS),
    )
