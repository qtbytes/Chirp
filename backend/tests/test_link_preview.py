import pytest
from app.services import link_preview as link_preview_service
from fastapi.testclient import TestClient
from main import app


@pytest.fixture(autouse=True)
def _disable_link_preview_cache(monkeypatch) -> None:
    # Keep these tests hermetic: never touch a real Redis (which could serve a
    # previously cached live preview instead of the stubbed fetch below).
    monkeypatch.setattr(link_preview_service, "get_redis_client", lambda: None)

SAMPLE_HTML = """
<html><head>
  <title>fallback title</title>
  <meta property="og:title" content="KhronosGroup/glslang">
  <meta property="og:description" content="Khronos-reference front end for GLSL/ESSL.">
  <meta property="og:image" content="/opengraph/glslang.png">
  <meta property="og:site_name" content="GitHub">
</head><body>ignored</body></html>
"""


def _register(username: str = "alice") -> TestClient:
    client = TestClient(app)
    client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "password123"},
    )
    return client


def test_link_preview_parses_open_graph(monkeypatch) -> None:
    monkeypatch.setattr(
        link_preview_service,
        "_fetch_html",
        lambda url: ("https://github.com/KhronosGroup/glslang", SAMPLE_HTML),
    )
    client = _register()

    response = client.get(
        "/api/v1/link-preview",
        params={"url": "https://github.com/KhronosGroup/glslang"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "KhronosGroup/glslang"
    assert body["description"] == "Khronos-reference front end for GLSL/ESSL."
    assert body["site_name"] == "GitHub"
    # relative og:image is resolved against the final URL
    assert body["image"] == "https://github.com/opengraph/glslang.png"
    # the card links back to the URL that was requested
    assert body["url"] == "https://github.com/KhronosGroup/glslang"


def test_link_preview_prefers_oembed_for_known_providers(monkeypatch) -> None:
    # A provider in the oEmbed registry (YouTube) is served from oEmbed JSON,
    # not by scraping its huge HTML page.
    assert (
        link_preview_service._oembed_endpoint("https://youtu.be/abc123")
        == "https://www.youtube.com/oembed"
    )

    def _fake_oembed(url: str, endpoint: str):
        from app.schemas.link_preview import LinkPreviewOut

        return LinkPreviewOut(
            url=url,
            title="Let's Talk about AppImage",
            image="https://i.ytimg.com/vi/x/hqdefault.jpg",
            site_name="YouTube",
        )

    def _no_scrape(url: str):  # pragma: no cover - must not be called
        raise AssertionError("known oEmbed providers must not be scraped")

    monkeypatch.setattr(link_preview_service, "_fetch_via_oembed", _fake_oembed)
    monkeypatch.setattr(link_preview_service, "_fetch_html", _no_scrape)
    client = _register()

    response = client.get(
        "/api/v1/link-preview",
        params={"url": "https://www.youtube.com/watch?v=x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Let's Talk about AppImage"
    assert body["site_name"] == "YouTube"


def test_best_thumbnail_upgrades_youtube_when_maxres_exists(monkeypatch) -> None:
    hq = "https://i.ytimg.com/vi/sRGT90fqh4g/hqdefault.jpg"

    # Non-YouTube thumbnails are left untouched (and never trigger a request).
    monkeypatch.setattr(
        link_preview_service,
        "_url_returns_image",
        lambda url: (_ for _ in ()).throw(AssertionError("should not check")),
    )
    assert link_preview_service._best_thumbnail("https://x.com/cover.png") == (
        "https://x.com/cover.png"
    )

    # Upgrade to maxres only when that file actually exists.
    monkeypatch.setattr(link_preview_service, "_url_returns_image", lambda url: True)
    assert link_preview_service._best_thumbnail(hq) == (
        "https://i.ytimg.com/vi/sRGT90fqh4g/maxresdefault.jpg"
    )

    monkeypatch.setattr(link_preview_service, "_url_returns_image", lambda url: False)
    assert link_preview_service._best_thumbnail(hq) == hq


def test_link_preview_without_metadata_returns_404(monkeypatch) -> None:
    monkeypatch.setattr(
        link_preview_service,
        "_fetch_html",
        lambda url: ("https://example.com", "<html><head></head><body>hi</body></html>"),
    )
    client = _register()

    response = client.get(
        "/api/v1/link-preview", params={"url": "https://example.com"}
    )
    assert response.status_code == 404


def test_link_preview_requires_auth() -> None:
    client = TestClient(app)
    response = client.get(
        "/api/v1/link-preview", params={"url": "https://example.com"}
    )
    assert response.status_code == 401


def test_url_is_fetchable_rejects_ssrf_targets() -> None:
    # Loopback/link-local/private IPs and non-http schemes are refused. These
    # all validate without a network round trip (literal IPs and localhost).
    for url in (
        "http://localhost/admin",
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "ftp://example.com/file",
        "file:///etc/passwd",
    ):
        assert link_preview_service._url_is_fetchable(url) is False


def test_link_preview_endpoint_refuses_loopback_without_fetching() -> None:
    # The real fetch rejects the loopback address before opening any connection,
    # so the endpoint returns "no preview" rather than hitting the local server.
    client = _register()
    response = client.get(
        "/api/v1/link-preview", params={"url": "http://127.0.0.1:8000/"}
    )
    assert response.status_code == 404
