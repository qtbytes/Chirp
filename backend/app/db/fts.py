"""
SQLite FTS5 full-text index over post content.

There is one source of truth for the FTS schema, used by two paths that both
need it:

- The test suite builds its database with ``Base.metadata.create_all()`` (see
  ``tests/conftest.py``), so the ``after_create`` / ``before_drop`` events below
  attach the virtual table and its triggers there.
- Production builds the schema with Alembic, so revision 0008 runs the same
  statement lists.

``posts_fts`` is an *external-content* table (``content='posts'``): it stores
only the inverted index, not a second copy of the text, and its ``rowid`` is the
``posts.id`` it indexes. The triggers keep it in sync on every write to
``posts``; the ``'delete'`` command form is FTS5's way of retracting a row from
an external-content index.

The indexed column is ``posts.search_text``, not ``posts.content``: it holds the
same text with CJK characters split into per-character tokens, which is what
makes Chinese and Japanese searchable from anywhere in a word rather than only
from its first character (see app/services/text_search.py). External-content
FTS5 reads the source column by name, so the virtual table's column, the
triggers and the backfill all have to name ``search_text``.
"""

from sqlalchemy import event, text

from app.db.database import Base

# Order matters: table, then triggers, then a backfill of any rows that already
# exist (a no-op on the empty create_all database, a real backfill under the
# migration).
SQLITE_FTS_CREATE: tuple[str, ...] = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts "
    "USING fts5(search_text, content='posts', content_rowid='id')",
    "CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN "
    "INSERT INTO posts_fts(rowid, search_text) VALUES (new.id, new.search_text); "
    "END",
    "CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN "
    "INSERT INTO posts_fts(posts_fts, rowid, search_text) "
    "VALUES ('delete', old.id, old.search_text); "
    "END",
    "CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN "
    "INSERT INTO posts_fts(posts_fts, rowid, search_text) "
    "VALUES ('delete', old.id, old.search_text); "
    "INSERT INTO posts_fts(rowid, search_text) VALUES (new.id, new.search_text); "
    "END",
    "INSERT INTO posts_fts(rowid, search_text) SELECT id, search_text FROM posts",
)

# Drop triggers before the table they reference.
SQLITE_FTS_DROP: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS posts_ai",
    "DROP TRIGGER IF EXISTS posts_ad",
    "DROP TRIGGER IF EXISTS posts_au",
    "DROP TABLE IF EXISTS posts_fts",
)


def include_name_excluding_fts(name, type_, parent_names) -> bool:
    """
    Alembic autogenerate/compare filter that hides the FTS artifacts.

    The virtual table ``posts_fts`` and the shadow tables SQLite creates for it
    (``posts_fts_data``, ``posts_fts_idx``, ``posts_fts_docsize``,
    ``posts_fts_config``) are not in ``Base.metadata`` -- they cannot be modelled
    -- so without this every autogenerate would want to drop them and the
    migration drift test would fail. All of them share the ``posts_fts`` prefix.
    """
    if type_ == "table" and name is not None and name.startswith("posts_fts"):
        return False
    return True


@event.listens_for(Base.metadata, "after_create")
def _create_fts(target, connection, **kw) -> None:
    if connection.dialect.name != "sqlite":
        return
    for statement in SQLITE_FTS_CREATE:
        connection.execute(text(statement))


@event.listens_for(Base.metadata, "before_drop")
def _drop_fts(target, connection, **kw) -> None:
    # Drop the FTS objects before ``drop_all`` tears down ``posts``: the virtual
    # table is not in the metadata, so ``drop_all`` leaves it behind, and the
    # next ``create_all`` would then find a stale index. Dropping here keeps each
    # test's index fresh.
    if connection.dialect.name != "sqlite":
        return
    for statement in SQLITE_FTS_DROP:
        connection.execute(text(statement))
