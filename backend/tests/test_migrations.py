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
from alembic.script import ScriptDirectory
from app.db.database import Base
from app.db.fts import include_name_excluding_fts
from app.models import (  # noqa: F401  -- populates Base.metadata
    FeedItem,
    Follow,
    Like,
    Notification,
    Post,
    User,
)

# The FTS5 virtual table and its shadow tables cannot be modelled, so filter them
# out of the drift comparison exactly as env.py does for autogenerate.
_COMPARE_OPTS = {"compare_type": True, "include_name": include_name_excluding_fts}

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
            context = MigrationContext.configure(connection, opts=_COMPARE_OPTS)
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
    The "for you" candidate fetch selects the newest top-level posts by
    ``created_at``, and profile timelines filter by ``user_id``. Both went
    unindexed in production.
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
        # Spelled out rather than built from Base.metadata. This fixture has to
        # describe the database as it stood *before* Alembic, and the models keep
        # moving: sourcing it from today's metadata made `users` sprout the email
        # columns, so revision 0003 then failed adding a column that was already
        # there. A frozen schema is the whole point of the test.
        connection.execute(
            sa.text(
                "CREATE TABLE users ("
                " id INTEGER NOT NULL,"
                " username VARCHAR(50) NOT NULL,"
                " password_hash VARCHAR(255) NOT NULL,"
                " created_at DATETIME NOT NULL,"
                " bio VARCHAR(160),"
                " avatar_url VARCHAR(255),"
                " display_name VARCHAR(50),"
                " PRIMARY KEY (id))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE follows ("
                " follower_id INTEGER NOT NULL,"
                " followee_id INTEGER NOT NULL,"
                " created_at DATETIME NOT NULL,"
                " CONSTRAINT pk_follows PRIMARY KEY (follower_id, followee_id),"
                " FOREIGN KEY(follower_id) REFERENCES users (id),"
                " FOREIGN KEY(followee_id) REFERENCES users (id))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE feed_items ("
                " id INTEGER NOT NULL,"
                " owner_id INTEGER NOT NULL,"
                " post_id INTEGER NOT NULL,"
                " actor_id INTEGER NOT NULL,"
                " created_at DATETIME NOT NULL,"
                " PRIMARY KEY (id),"
                " CONSTRAINT uq_feed_owner_post UNIQUE (owner_id, post_id),"
                " FOREIGN KEY(owner_id) REFERENCES users (id),"
                " FOREIGN KEY(post_id) REFERENCES posts (id),"
                " FOREIGN KEY(actor_id) REFERENCES users (id))"
            )
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
            "CREATE UNIQUE INDEX ix_users_username ON users (username)",
            "CREATE INDEX ix_follows_followee_created ON follows (followee_id, created_at)",
            "CREATE INDEX ix_feed_items_owner_id ON feed_items (owner_id)",
            "CREATE INDEX ix_feed_owner_created_id ON feed_items (owner_id, created_at, id)",
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

            context = MigrationContext.configure(connection, opts=_COMPARE_OPTS)
            difference = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert content == "survives the migration", "the rebuild dropped existing rows"
    assert likes == 1, "the likes rebuild dropped existing rows"
    # Asked of Alembic rather than hardcoded, so adding a revision does not
    # require editing this assertion.
    assert version == ScriptDirectory.from_config(_alembic_config(url)).get_current_head()
    assert "retweets" not in table_names, "the vestigial table should be gone"
    assert {"ix_posts_created_at", "ix_posts_user_id"} <= post_indexes
    assert difference == [], f"adopted database still differs from the models: {difference}"
