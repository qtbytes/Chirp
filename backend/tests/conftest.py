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
def isolated_sessions(monkeypatch) -> None:
    """
    Keep sessions in-process during tests.

    Otherwise every test that logs in writes `session:*` keys into the
    developer's real Redis, sharing a keyspace with their browser session.
    Tests that want the real backend undo this (see test_sessions.py).
    """
    from app.core import session_store

    monkeypatch.setattr(session_store, "get_redis_client", lambda: None)
    session_store._memory_sessions.clear()
