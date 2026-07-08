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

from app.db.redis_client import get_redis_client
from app.schemas.link_preview import LinkPreviewOut

_USER_AGENT = "ChirpLinkPreview/1.0 (+https://github.com/) bot"
_REQUEST_TIMEOUT = 5.0
_MAX_REDIRECTS = 5
_MAX_BYTES = 512 * 1024  # only need the <head>, so cap the download hard

_CACHE_PREFIX = "linkpreview:"
_POSITIVE_TTL = 24 * 60 * 60  # a good card rarely changes; cache it a day
_NEGATIVE_TTL = 60 * 60  # remember "no card here" briefly to avoid re-fetch storms


# --- SSRF guards -----------------------------------------------------------


def _host_is_safe(host: str) -> bool:
    """True only if every address ``host`` resolves to is publicly routable."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
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
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=_REQUEST_TIMEOUT,
            headers=headers,
        ) as client:
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

                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) >= _MAX_BYTES:
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
