"""JSON-lines logging to stdout with a redaction filter.

The filter masks configured secrets, anything that looks like a bot token,
Cookie / Authorization headers, and strips query strings from URLs (signed
CDN URLs carry credentials in the query).
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Iterable

_STD_ATTRS = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {
    "message",
    "asctime",
}
_URL_QUERY = re.compile(r"(https?://[^\s?#'\"<>]+)\?[^\s'\"<>]*", re.IGNORECASE)
_HEADER = re.compile(r"(?i)\b(cookie|set-cookie|authorization|x-csrftoken)(\s*[:=]\s*)[^\r\n]+")
_TOKEN_LIKE = re.compile(r"\b\d{5,12}:[A-Za-z0-9_-]{30,}\b")


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = _TOKEN_LIKE.sub("<redacted-token>", text)
    text = _HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _URL_QUERY.sub(r"\1?<redacted>", text)
    return text


class RedactFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = tuple(s for s in secrets if s and len(s) >= 6)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # bad format args should never kill logging
            msg = str(record.msg)
        record.msg = redact(msg, self.secrets)
        record.args = None
        if record.exc_info:
            record.exc_text = redact(
                logging.Formatter().formatException(record.exc_info), self.secrets
            )
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text, self.secrets)
        for key, value in list(vars(record).items()):
            if key not in _STD_ATTRS and isinstance(value, str):
                setattr(record, key, redact(value, self.secrets))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key in _STD_ATTRS or key.startswith("_"):
                continue
            data[key] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        if record.exc_text:
            data["exc"] = record.exc_text
        if record.stack_info:
            data["stack"] = record.stack_info
        return json.dumps(data, ensure_ascii=False)


def setup_logging(level: str = "INFO", fmt: str = "json", secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactFilter(secrets))
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Pyrogram/Kurigram is chatty at INFO.
    logging.getLogger("pyrogram").setLevel("DEBUG" if level == "DEBUG" else "WARNING")
