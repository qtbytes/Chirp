from collections.abc import Generator

from app.core.config import settings
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")

connect_args = {}
if _is_sqlite:
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        """
        The API process and the RQ fan-out worker write to the same SQLite file.
        Without WAL the worker's ``feed_items`` insert blocks readers, and
        without a busy timeout a concurrent write fails immediately with
        "database is locked" instead of waiting for the other writer.

        Foreign keys stay off on purpose: deletes rely on the manual cascade in
        ``post_repository.delete_post``.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()
SessionLocal = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, class_=Session
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
