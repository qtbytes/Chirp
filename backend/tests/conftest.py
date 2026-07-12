import tempfile
from collections.abc import Generator

import pytest
from app.core.config import settings

# Redirect uploads to a throwaway directory BEFORE importing `main` (which
# mounts StaticFiles against settings.uploads_dir at import time). Without this,
# the avatar tests write to the real ./uploads and — because a fresh test user
# can get the same id as a real user — the upload endpoint's "remove previous
# avatar" glob deletes real dev avatar files. See test_avatar_* in
# test_profile_api.py.
settings.uploads_dir = tempfile.mkdtemp(prefix="chirp-test-uploads-")

# Redis is a hard dependency now (no in-memory fallback), so the suite talks to a
# real Redis -- but on a dedicated logical DB so it never touches the developer's
# working keyspace (db 0). Set before anything builds a client, and drop any
# cached client so it reconnects against this DB.
settings.redis_url = "redis://localhost:6379/15"

from app.db import redis_client as _redis_client  # noqa: E402

_redis_client._client = None

from app.db.database import Base, get_db  # noqa: E402
from main import app  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
)


def override_get_db() -> Generator[Session, None, None]:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
settings.rate_limit_enabled = False


@pytest.fixture(autouse=True)
def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def flush_test_redis() -> Generator[None, None, None]:
    """
    Start every test with an empty Redis keyspace.

    Sessions, tokens, caches, and the fan-out queue all share the test DB (15);
    flushing between tests keeps them isolated -- a session minted by one test, or
    the single global trending-cache key, cannot leak into the next.
    """
    from app.db.redis_client import get_redis_client

    client = get_redis_client()
    client.flushdb()
    yield
    client.flushdb()
