from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config import settings
from app.db.database import Base, engine
from app.db.fts import include_name_excluding_fts

# Importing the models is what populates Base.metadata, which autogenerate
# diffs against the live database. Without this every revision comes out empty.
from app.models import (  # noqa: F401
    FeedItem,
    Follow,
    Like,
    Notification,
    Post,
    PostHashtag,
    PostMention,
    User,
)

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers would otherwise silence pytest's capture when the
    # migration test invokes `command.upgrade` in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url_override() -> str | None:
    """
    A database URL supplied by the caller, if any.

    alembic.ini deliberately leaves ``sqlalchemy.url`` unset, so this is normally
    None and the app's own settings win. Setting it programmatically lets the
    migration tests point at a scratch file rather than the developer's
    twitter.db.
    """
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (``alembic upgrade head --sql``)."""
    context.configure(
        url=_url_override() or settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most things in place; batch mode rewrites the table
        # instead. It is a no-op on backends that can, so both modes set it.
        render_as_batch=True,
        compare_type=True,
        # The FTS5 virtual table and its shadow tables are not modelled, so keep
        # autogenerate from proposing to drop them.
        include_name=include_name_excluding_fts,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Reuse the application engine so migrations connect exactly the way the API
    # does: same URL, same SQLite pragmas (WAL, busy_timeout). An explicit
    # sqlalchemy.url override means a caller wants a different database, so it
    # gets its own engine.
    override = _url_override()
    connectable = create_engine(override) if override else engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            include_name=include_name_excluding_fts,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
