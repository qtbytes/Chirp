"""
Generic link unfurling: turn a URL into a preview card from the page's
Open Graph / Twitter Card metadata.

Why a single generic unfurler (and not a per-site adapter):
- GitHub, YouTube, Steam and the long tail of the web all expose the same
  standard ``<meta property="og:*">`` / ``<meta name="twitter:*">`` tags, so one
  parser produces a card for essentially any site.
- Per-site code is only needed for the edge cases (anti-scraping, auth-walled,
  or JS-rendered pages that ship no OG tags) — not the common case.

Safety is the hard part, not parsing. Because we fetch arbitrary user-supplied
URLs *server-side*, this module:
- allows only http/https,
- resolves each host and refuses private / loopback / link-local / reserved
  addresses (SSRF — e.g. ``169.254.169.254`` cloud metadata, ``127.0.0.1``),
- re-validates every redirect hop,
- caps bytes read and request time,
- caches results (including negatives) in Redis to avoid re-fetching.
"""

import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings
from app.db.redis_client import get_redis_client
from app.schemas.link_preview import LinkPreviewOut

_USER_AGENT = "ChirpLinkPreview/1.0 (+https://github.com/) bot"
_REQUEST_TIMEOUT = 8.0
_MAX_REDIRECTS = 5
# Hard ceiling on how much of a page we pull. We stop earlier at </head>; this
# only bounds pathological pages whose head never closes. 1 MB comfortably
# covers script-heavy pages (YouTube's OG tags sit near ~640 KB).
_MAX_BYTES = 1024 * 1024
_HEAD_CLOSE = b"</head>"
_HEAD_CLOSE_LEN = len(_HEAD_CLOSE)

_CACHE_PREFIX = "linkpreview:"
_POSITIVE_TTL = 24 * 60 * 60  # a good card rarely changes; cache it a day
_NEGATIVE_TTL = 60 * 60  # remember "no card here" briefly to avoid re-fetch storms


# --- SSRF guards -----------------------------------------------------------
#
# Two layers, because they defend different things:
#   1. Always-on checks that need no DNS — the scheme, IP-literal hosts, and an
#      internal-hostname denylist. These catch the direct SSRF a user could type
#      (http://169.254.169.254, http://127.0.0.1, http://localhost, ...).
#   2. An optional DNS-resolution check (settings.link_preview_verify_dns) that
#      rejects hostnames resolving to internal IPs. It is disabled behind a
#      fake-IP proxy (e.g. Clash/Surge TUN mode), where DNS returns synthetic
#      addresses in 198.18.0.0/15 for *every* host — there the check is both
#      wrong (blocks legit sites) and useless (the app never connects to the
#      resolved IP; the proxy does).

_BLOCKED_HOST_NAMES = {"localhost"}
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal")


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_is_safe(host: str) -> bool:
    """True if ``host`` is not an obviously-internal target we must not fetch."""
    host = host.strip(".").lower()
    if not host:
        return False

    # IP-literal host: judge it directly. No DNS, so a proxy's synthetic
    # addresses can't interfere, and this still blocks 127.0.0.1 / 10.x / etc.
    try:
        return not _ip_is_blocked(ipaddress.ip_address(host))
    except ValueError:
        pass

    if host in _BLOCKED_HOST_NAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False

    # Hostname → resolved-IP check. Skipped when disabled, or when an outbound
    # proxy is configured (the proxy — not this app — resolves and connects, so
    # local resolution is both meaningless and, behind fake-IP DNS, wrong).
    if not settings.link_preview_verify_dns or settings.link_preview_http_proxy:
        return True

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        if _ip_is_blocked(ipaddress.ip_address(info[4][0])):
            return False
    return True


def _url_is_fetchable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    return _host_is_safe(parsed.hostname)


# --- HTML metadata parsing -------------------------------------------------


class _MetaParser(HTMLParser):
    """Collects og:*/twitter:* meta, the <title>, and a favicon from a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self.icon: str | None = None
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        attr = {name: value for name, value in attrs if value is not None}
        if tag == "meta":
            key = attr.get("property") or attr.get("name")
            content = attr.get("content")
            if key and content and key not in self.meta:
                key_lower = key.lower()
                if (
                    key_lower.startswith("og:")
                    or key_lower.startswith("twitter:")
                    or key_lower == "description"
                ):
                    self.meta[key_lower] = content
        elif tag == "title":
            self._in_title = True
        elif tag == "link":
            rel = (attr.get("rel") or "").lower()
            if "icon" in rel and attr.get("href") and self.icon is None:
                self.icon = attr.get("href")

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            text = data.strip()
            if text:
                self.title = text

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            # Everything we care about lives in <head>; ignore the body.
            self._done = True


def _absolute_http_url(candidate: str | None, base_url: str) -> str | None:
    """Resolve ``candidate`` against ``base_url`` and keep it only if http(s)."""
    if not candidate:
        return None
    resolved = urljoin(base_url, candidate.strip())
    if urlparse(resolved).scheme in ("http", "https"):
        return resolved
    return None


def _build_preview(
    requested_url: str, final_url: str, html: str
) -> LinkPreviewOut | None:
    parser = _MetaParser()
    try:
        parser.feed(html)
    except Exception:
        # A malformed page should degrade to "no preview", never crash the API.
        return None

    meta = parser.meta
    title = meta.get("og:title") or meta.get("twitter:title") or parser.title
    if not title:
        # Without at least a title there is nothing worth showing as a card.
        return None

    description = (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
    )
    image = _absolute_http_url(
        meta.get("og:image")
        or meta.get("twitter:image")
        or meta.get("twitter:image:src"),
        final_url,
    )
    site_name = meta.get("og:site_name") or urlparse(final_url).hostname

    return LinkPreviewOut(
        url=requested_url,
        title=title[:300],
        description=description[:500] if description else None,
        image=image,
        site_name=site_name,
    )


# --- fetching --------------------------------------------------------------


def _fetch_html(url: str) -> tuple[str, str] | None:
    """
    Fetch an HTML document, validating SSRF safety on the initial URL and every
    redirect hop. Returns ``(final_url, html)`` or ``None`` if the URL is unsafe,
    not HTML, or unreachable.
    """
    current = url
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"}
    client_kwargs: dict = {
        "follow_redirects": False,
        "timeout": _REQUEST_TIMEOUT,
        "headers": headers,
    }
    if settings.link_preview_http_proxy:
        client_kwargs["proxy"] = settings.link_preview_http_proxy
    try:
        with httpx.Client(**client_kwargs) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                if not _url_is_fetchable(current):
                    return None
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return None
                        current = urljoin(str(response.url), location)
                        continue
                    if response.status_code >= 400:
                        return None
                    content_type = response.headers.get("content-type", "")
                    if "html" not in content_type.lower():
                        return None

                    # Read only as far as we need. All the metadata lives in
                    # <head>, so stop once it closes — some pages (YouTube) put
                    # ~600 KB of inline script before their OG tags, so we can't
                    # cut too early, but we also shouldn't pull whole MB bodies.
                    chunks = bytearray()
                    scan_from = 0
                    done = False
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        window = chunks[max(0, scan_from - _HEAD_CLOSE_LEN) :].lower()
                        if _HEAD_CLOSE in window:
                            done = True
                        scan_from = len(chunks)
                        if done or len(chunks) >= _MAX_BYTES:
                            break
                    html = bytes(chunks).decode(
                        response.encoding or "utf-8", errors="replace"
                    )
                    return str(response.url), html
    except httpx.HTTPError:
        return None
    return None


# --- caching ---------------------------------------------------------------


def _cache_key(url: str) -> str:
    return f"{_CACHE_PREFIX}{url}"


def _cache_get(url: str) -> tuple[bool, LinkPreviewOut | None]:
    """Return ``(hit, preview)``; an empty cached value is a negative result."""
    client = get_redis_client()
    if client is None:
        return (False, None)
    raw = client.get(_cache_key(url))
    if raw is None:
        return (False, None)
    if raw == b"":
        return (True, None)
    try:
        return (True, LinkPreviewOut.model_validate_json(raw))
    except ValueError:
        return (False, None)


def _cache_set(url: str, preview: LinkPreviewOut | None) -> None:
    client = get_redis_client()
    if client is None:
        return
    if preview is None:
        client.setex(_cache_key(url), _NEGATIVE_TTL, b"")
    else:
        client.setex(_cache_key(url), _POSITIVE_TTL, preview.model_dump_json())


# --- public API ------------------------------------------------------------


def fetch_link_preview(url: str) -> LinkPreviewOut | None:
    """
    Return a preview card for ``url``, or ``None`` when there is nothing to show
    (unsafe URL, unreachable, or no usable metadata). Results are cached.
    """
    url = url.strip()
    hit, cached = _cache_get(url)
    if hit:
        return cached

    preview: LinkPreviewOut | None = None
    fetched = _fetch_html(url)
    if fetched is not None:
        final_url, html = fetched
        preview = _build_preview(url, final_url, html)

    _cache_set(url, preview)
    return preview
