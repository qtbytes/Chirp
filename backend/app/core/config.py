from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SESSION_SECRET = "dev-session-secret-change-me"


def resolve_database_url(database_url: str) -> str:
    sqlite_prefix = "sqlite:///"
    if not database_url.startswith(sqlite_prefix):
        return database_url

    sqlite_path = database_url[len(sqlite_prefix) :]
    if not sqlite_path or sqlite_path == ":memory:":
        return database_url

    candidate = Path(sqlite_path)
    if candidate.is_absolute():
        return database_url

    resolved_path = (PROJECT_ROOT / candidate).resolve()
    return f"{sqlite_prefix}{resolved_path.as_posix()}"


class Settings(BaseSettings):
    app_name: str = "FastAPI Twitter System"
    database_url: str = "sqlite:///./twitter.db"
    redis_url: str = "redis://localhost:6379/0"
    frontend_origin: str = "http://localhost:5173"
    session_cookie_name: str = "twitter_session"
    session_secret_key: str = DEFAULT_SESSION_SECRET
    # Must be True wherever the site is served over HTTPS, otherwise the
    # session cookie is sent on plaintext requests too.
    session_cookie_secure: bool = False
    dev_auto_sync_sqlite_schema: bool = True
    # Honour the X-User-Id header as authentication. Local convenience only:
    # the header is client-supplied, so enabling it lets anyone impersonate
    # any user. Never enable on a network-reachable deployment.
    dev_allow_header_auth: bool = False
    uploads_dir: str = str(PROJECT_ROOT / "uploads")

    timeline_cache_ttl_seconds: int = 30
    default_timeline_strategy: str = "read"
    timeline_page_size: int = 20

    rq_queue_name: str = "feed-fanout"
    rq_job_timeout_seconds: int = 600
    run_fanout_inline_when_queue_unavailable: bool = True

    # Link previews fetch arbitrary user URLs server-side.
    # - Set link_preview_http_proxy to route those fetches through an outbound
    #   proxy (e.g. "http://127.0.0.1:7897" for Clash/Surge). Behind such a
    #   proxy the proxy resolves and connects, so the local resolved-IP SSRF
    #   check is skipped automatically.
    # - link_preview_verify_dns toggles the resolved-IP check for direct
    #   (no-proxy) fetches. Scheme, IP-literal, and internal-hostname blocking
    #   stay on regardless of both settings.
    link_preview_http_proxy: str | None = None
    link_preview_verify_dns: bool = True

    rate_limit_enabled: bool = True
    rate_limit_post_tweet_max_requests: int = 10
    rate_limit_post_tweet_window_seconds: int = 60
    rate_limit_like_max_requests: int = 60
    rate_limit_like_window_seconds: int = 60
    rate_limit_comment_max_requests: int = 30
    rate_limit_comment_window_seconds: int = 60
    rate_limit_timeline_max_requests: int = 120
    rate_limit_timeline_window_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
settings.database_url = resolve_database_url(settings.database_url)

if settings.session_cookie_secure and settings.dev_allow_header_auth:
    raise RuntimeError(
        "DEV_ALLOW_HEADER_AUTH must be false in production: the X-User-Id "
        "header is client-supplied and would let anyone impersonate any user."
    )

if settings.session_cookie_secure and settings.session_secret_key == DEFAULT_SESSION_SECRET:
    # The session cookie is just base64(user_id) + HMAC(secret). With the
    # published default secret anyone can forge a cookie for any user id.
    raise RuntimeError(
        "SESSION_SECRET_KEY is still the development default. Set a random "
        "value (e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`) "
        "before serving over HTTPS."
    )
