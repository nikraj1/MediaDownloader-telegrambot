"""Optional per-site cookie files.

COOKIES_DIR is mounted read-only. A file is used only when the URL's domain
matches; it is copied into the job dir because yt-dlp writes cookies back on
exit. Contents and paths are never logged or forwarded to other domains.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

COOKIE_FILES = {
    "instagram.com": "instagram.txt",
    "facebook.com": "facebook.txt",
    "fb.watch": "facebook.txt",
    "youtube.com": "youtube.txt",
    "youtu.be": "youtube.txt",
    "x.com": "x.txt",
    "twitter.com": "x.txt",
    "tiktok.com": "tiktok.txt",
    "reddit.com": "reddit.txt",
    "vimeo.com": "vimeo.txt",
}


def cookie_name_for(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for domain, name in COOKIE_FILES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def prepare(url: str, cookies_dir: Path, job_dir: Path) -> Path | None:
    name = cookie_name_for(url)
    if not name:
        return None
    src = cookies_dir / name
    if not src.is_file():
        return None
    dst = job_dir / "cookies.txt"
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o600)
    return dst


def status(cookies_dir: Path) -> list[tuple[str, str]]:
    out = []
    for name in sorted(set(COOKIE_FILES.values())):
        path = cookies_dir / name
        if path.is_file():
            mtime = time.strftime("%Y-%m-%d", time.gmtime(path.stat().st_mtime))
            out.append((name, mtime))
    return out
