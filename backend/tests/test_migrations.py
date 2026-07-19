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
