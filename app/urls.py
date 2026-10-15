"""URL extraction from Telegram messages and normalisation.

Entities (url, text_link) are preferred; a regex is the fallback. Only the
first URL is used. Telegram entity offsets are UTF-16 code units.
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import BotError, ErrorCategory as C

MAX_URL_LEN = 2048
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'`]+")
_TRAILING = ".,;:!?'\"»”’>]}"
TRACKING_PARAMS = frozenset(
    {
        "igsh", "igshid", "si", "fbclid", "gclid", "dclid", "mibextid", "ref_src",
        "ref_url", "mc_cid", "mc_eid", "yclid", "_hsenc", "_hsmi", "share_id", "rdid",
    }
)
TRACKING_PREFIXES = ("utm_",)


def _u16_slice(text: str, offset: int, length: int) -> str:
    data = text.encode("utf-16-le")
    return data[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")


def _clean_tail(url: str) -> str:
    while url:
        ch = url[-1]
        if ch == ")":
            if url.count("(") >= url.count(")"):
                break
            url = url[:-1]
            continue
        if ch in _TRAILING:
            url = url[:-1]
            continue
        break
    return url


def _entity_type(entity: object) -> str:
    kind = getattr(entity, "type", None)
    return str(getattr(kind, "name", kind) or "").upper()


def find_urls(text: str | None, entities: Iterable[object] | None = None) -> list[str]:
    text = text or ""
    found: list[str] = []
    for entity in entities or []:
        kind = _entity_type(entity)
        if kind == "TEXT_LINK" and getattr(entity, "url", None):
            found.append(entity.url)  # type: ignore[attr-defined]
        elif kind == "URL":
            found.append(_u16_slice(text, entity.offset, entity.length))  # type: ignore[attr-defined]
    if not found:
        found = [_clean_tail(m.group(0)) for m in _URL_RE.finditer(text)]
    return [u.strip() for u in found if u and u.strip()]


def first_url(text: str | None, entities: Iterable[object] | None = None) -> str | None:
    urls = find_urls(text, entities)
    return urls[0] if urls else None


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in TRACKING_PARAMS or k.startswith(TRACKING_PREFIXES)


def normalise_url(raw: str) -> str:
    url = raw.strip()
    if len(url) > MAX_URL_LEN:
        raise BotError(C.INVALID_URL, "url too long")
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        _ = parts.port  # validates the port
    except ValueError:
        raise BotError(C.INVALID_URL, "unparseable url") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise BotError(C.INVALID_URL, f"scheme {scheme!r} not allowed")
    if not parts.hostname:
        raise BotError(C.INVALID_URL, "no host")
    userinfo, sep, hostport = parts.netloc.rpartition("@")
    netloc = f"{userinfo}{sep}{hostport.lower()}"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)],
        doseq=True,
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))
