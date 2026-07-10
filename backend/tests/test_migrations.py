"""
Guard the property that the old startup ``create_all()`` quietly broke.

The dev and production databases spent a long time missing two indexes that
``Post`` declared, because nothing ever compared the live schema to the models.
The suite builds its tables with ``create_all()`` (see ``conftest.py``), so it
would not have noticed either. These tests run the real migrations instead.
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from app.db.database import Base
from app.models import (  # noqa: F401  -- populates Base.metadata
    FeedItem,
    Follow,
    Like,
    Notification,
    Post,
    User,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    # alembic.ini leaves this unset so that env.py falls back to app settings --
    # i.e. the developer's real twitter.db. Point it at the scratch file instead.
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def migrated_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    command.upgrade(_alembic_config(url), "head")
    return url


def test_migrations_produce_the_schema_the_models_describe(migrated_url: str) -> None:
    """
    `alembic check`, as a test.

    Fails when a model changes without a matching revision -- the drift that let
    ix_posts_created_at and ix_posts_user_id go missing for the life of the app.
    """
    engine = sa.create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True}
            )
            difference = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert difference == [], (
        "migrations and models disagree; run "
        "`uv run alembic revision --autogenerate` to record the change:\n"
        f"{difference}"
    )


def test_migrations_index_the_columns_the_feed_queries_sort_by(
    migrated_url: str,
) -> None:
    """
    ``list_for_you_tweets`` orders every top-level post by ``created_at``, and
    profile timelines filter by ``user_id``. Both went unindexed in production.
    """
    engine = sa.create_engine(migrated_url)
    try:
        indexes = {index["name"] for index in sa.inspect(engine).get_indexes("posts")}
    finally:
        engine.dispose()

    assert "ix_posts_created_at" in indexes
    assert "ix_posts_user_id" in indexes


def test_upgrade_adopts_a_pre_alembic_database(tmp_path: Path) -> None:
    """
    A database with tables but no alembic_version is stamped, not recreated.

    Reproduces the shape of the real thing, as dumped from the dev and pruned
    production databases: ``posts``, ``likes`` and ``notifications`` hand-created
    with raw SQL -- so they carry TIMESTAMP columns, nullable ``INTEGER PRIMARY
    KEY`` ids, no foreign key on ``quoted_post_id``, and none of the
    single-column indexes -- while ``users``, ``follows`` and ``feed_items`` came
    from ``create_all()`` and already match. The dead ``retweets`` table lingers.

    The upgrade must keep the rows, converge every table on the models, and drop
    ``retweets`` -- rather than failing on ``CREATE TABLE users``.
    """
    database = tmp_path / "legacy.db"
    url = f"sqlite:///{database.as_posix()}"

    legacy = sa.create_engine(url)
    with legacy.begin() as connection:
        # These three matched the models already; create_all() is how they got there.
        Base.metadata.create_all(
            bind=connection,
            tables=[
                Base.metadata.tables[name]
                for name in ("users", "follows", "feed_items")
            ],
        )
        connection.execute(
            sa.text(
                "CREATE TABLE posts ("
                " id INTEGER PRIMARY KEY,"
                " user_id INTEGER NOT NULL REFERENCES users(id),"
                " content TEXT NOT NULL,"
                " media_urls JSON,"
                " created_at TIMESTAMP NOT NULL,"
                " reply_to_id INTEGER REFERENCES posts(id),"
                " root_id INTEGER REFERENCES posts(id),"
                " edited_at DATETIME,"
                " quoted_post_id INTEGER)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE likes ("
                " user_id INTEGER NOT NULL REFERENCES users(id),"
                " post_id INTEGER NOT NULL REFERENCES posts(id),"
                " created_at TIMESTAMP NOT NULL,"
                " PRIMARY KEY (user_id, post_id))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE notifications ("
                " id INTEGER PRIMARY KEY,"
                " user_id INTEGER NOT NULL REFERENCES users(id),"
                " actor_id INTEGER NOT NULL REFERENCES users(id),"
                " type VARCHAR(32) NOT NULL,"
                " post_id INTEGER REFERENCES posts(id),"
                " is_read BOOLEAN NOT NULL DEFAULT 0,"
                " created_at DATETIME NOT NULL)"
            )
        )
        for statement in (
            "CREATE INDEX ix_notifications_created_at ON notifications (created_at)",
            "CREATE INDEX ix_notifications_user_created ON notifications (user_id, created_at)",
            "CREATE INDEX ix_notifications_user_id ON notifications (user_id)",
            "CREATE INDEX ix_posts_reply_created ON posts (reply_to_id, created_at)",
            "CREATE INDEX ix_posts_root_created ON posts (root_id, created_at)",
            "CREATE INDEX ix_posts_user_created ON posts (user_id, created_at)",
            "CREATE TABLE retweets ("
            " user_id INTEGER NOT NULL,"
            " post_id INTEGER NOT NULL,"
            " created_at DATETIME NOT NULL,"
            " PRIMARY KEY (user_id, post_id))",
        ):
            connection.execute(sa.text(statement))

        connection.execute(
            sa.text(
                "INSERT INTO users (id, username, password_hash, created_at)"
                " VALUES (1, 'dev', 'hash', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO posts (id, user_id, content, created_at)"
                " VALUES (1, 1, 'survives the migration', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO likes (user_id, post_id, created_at)"
                " VALUES (1, 1, '2026-01-01 00:00:00')"
            )
        )
    legacy.dispose()

    command.upgrade(_alembic_config(url), "head")

    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        post_indexes = {index["name"] for index in inspector.get_indexes("posts")}
        with engine.connect() as connection:
            content = connection.execute(
                sa.text("SELECT content FROM posts WHERE id = 1")
            ).scalar_one()
            likes = connection.execute(sa.text("SELECT COUNT(*) FROM likes")).scalar_one()
            version = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

            context = MigrationContext.configure(
                connection, opts={"compare_type": True}
            )
            difference = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert content == "survives the migration", "the rebuild dropped existing rows"
    assert likes == 1, "the likes rebuild dropped existing rows"
    assert version == "0002"
    assert "retweets" not in table_names, "the vestigial table should be gone"
    assert {"ix_posts_created_at", "ix_posts_user_id"} <= post_indexes
    assert difference == [], f"adopted database still differs from the models: {difference}"
