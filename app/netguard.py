"""SSRF guard: scheme, userinfo, port, host and resolved-IP checks, plus a
manual redirect pre-flight for hosts that no known extractor handles.

This gives friendly errors and stops obvious abuse. It can NOT see the many
requests yt-dlp makes afterwards, so the host egress firewall
(deploy/egress-firewall.sh) is the real backstop, including for DNS rebinding.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

from .errors import BotError, ErrorCategory as C

log = logging.getLogger(__name__)

ALLOWED_PORTS = {None, 80, 443}
MAX_REDIRECTS = 5
_UA = "Mozilla/5.0 (X11; Linux x86_64) MediaDownloaderBot/2.0"
_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]*|\d+)(?:\.(?:0x[0-9a-f]*|\d+)){0,3}$", re.I)

BLOCKED_HOSTS = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "metadata",
     "metadata.google.internal", "instance-data"}
)
BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa", ".intranet",
                    ".corp", ".localdomain")

# Hosts handled by dedicated yt-dlp extractors. Their own redirects happen
# inside the extractor, so no pre-flight is done for them.
KNOWN_EXTRACTOR_DOMAINS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "instagram.com", "facebook.com",
    "fb.watch", "fb.com", "x.com", "twitter.com", "t.co", "tiktok.com", "reddit.com",
    "redd.it", "vimeo.com", "dailymotion.com", "dai.ly", "soundcloud.com", "twitch.tv",
    "streamable.com", "pinterest.com", "pin.it", "bilibili.com", "b23.tv", "threads.net",
    "threads.com", "snapchat.com", "linkedin.com", "tumblr.com", "imgur.com", "bsky.app",
    "rumble.com", "odysee.com", "bandcamp.com", "mixcloud.com", "vk.com", "ok.ru",
)


def is_known_host(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in KNOWN_EXTRACTOR_DOMAINS)


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip_is_blocked(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return ip_is_blocked(ip.sixtofour)
        if ip.teredo is not None:
            return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_ip_literal(host: str) -> bool:
    h = host.strip("[]")
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    if _NUMERIC_HOST.match(h):  # decimal / hex / octal / shortened IPv4 forms
        return True
    try:
        socket.inet_aton(h)
        return True
    except OSError:
        return False


def check_static(url: str) -> tuple[str, int | None, str]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BotError(C.INVALID_URL, "unparseable url or port") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise BotError(C.BLOCKED_URL, f"scheme {scheme!r}")
    if "@" in parts.netloc or parts.username or parts.password:
        raise BotError(C.BLOCKED_URL, "userinfo in url")
    if port not in ALLOWED_PORTS:
        raise BotError(C.BLOCKED_URL, f"port {port}")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise BotError(C.INVALID_URL, "no host")
    if _is_ip_literal(host):
        raise BotError(C.BLOCKED_URL, "ip literal host")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES) or "." not in host:
        raise BotError(C.BLOCKED_URL, f"internal host {host}")
    return host, port, scheme


def resolve_and_check(host: str, port: int | None, scheme: str) -> None:
    try:
        infos = socket.getaddrinfo(host, port or (443 if scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BotError(C.NETWORK, f"dns failure for {host}: {exc}") from None
    if not infos:
        raise BotError(C.NETWORK, f"no addresses for {host}")
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if ip_is_blocked(ip):
            raise BotError(C.BLOCKED_URL, f"{host} resolves to non-public {ip}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _hop(url: str) -> str | None:
    """One request without following redirects. Returns the Location, if any."""
    for method in ("HEAD", "GET"):
        headers = {"User-Agent": _UA, "Accept": "*/*"}
        if method == "GET":
            headers["Range"] = "bytes=0-0"
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with _OPENER.open(req, timeout=10):
                return None
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                return exc.headers.get("Location")
            if method == "HEAD" and exc.code in (400, 403, 404, 405, 501):
                continue
            return None
    return None


def _preflight(url: str) -> None:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        host, port, scheme = check_static(current)
        resolve_and_check(host, port, scheme)
        try:
            location = _hop(current)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Unreachable is not "blocked": let yt-dlp produce the real error.
            log.info("preflight request failed", extra={"err": type(exc).__name__})
            return
        if not location:
            return
        current = urljoin(current, location)
    raise BotError(C.BLOCKED_URL, "too many redirects")


def check_url_sync(url: str, preflight_unknown: bool = True) -> str:
    host, port, scheme = check_static(url)
    resolve_and_check(host, port, scheme)
    if preflight_unknown and not is_known_host(host):
        _preflight(url)
    return host


async def check_url(url: str, timeout: float = 30.0) -> str:
    try:
        return await asyncio.wait_for(asyncio.to_thread(check_url_sync, url), timeout)
    except TimeoutError:
        raise BotError(C.NETWORK, "url check timed out") from None
