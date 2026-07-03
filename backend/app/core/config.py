from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    session_secret_key: str = "dev-session-secret-change-me"
    dev_auto_sync_sqlite_schema: bool = True

    timeline_cache_ttl_seconds: int = 30
    default_timeline_strategy: str = "read"
    timeline_page_size: int = 20

    rq_queue_name: str = "feed-fanout"
    rq_job_timeout_seconds: int = 600
    run_fanout_inline_when_queue_unavailable: bool = True

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
