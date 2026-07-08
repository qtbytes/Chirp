from app.services import link_preview as link_preview_service
from fastapi.testclient import TestClient
from main import app

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
