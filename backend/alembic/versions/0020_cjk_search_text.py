"""index posts on a CJK-segmented copy of their content

FTS5's ``unicode61`` tokenizer reads a run of Han characters as one token, so
``中文测试`` was findable from ``中文`` but never from ``测试``. This revision adds
``posts.search_text`` -- the post's content with each CJK character split into
its own token (see ``app/services/text_search.py``) -- backfills it, and rebuilds
``posts_fts`` over that column instead of ``posts.content``.

The rebuild is a drop and re-create rather than an in-place change: an FTS5
virtual table's column list is fixed at creation, and the index has to be
re-derived from the segmented text anyway. Nothing is lost -- the table stores
only the inverted index, and the trailing statement in ``SQLITE_FTS_CREATE``
rebuilds it from ``posts``.

SQLite-only, like revision 0008: on any other backend the column is added and
the FTS block skipped.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.fts import SQLITE_FTS_CREATE, SQLITE_FTS_DROP
from app.services.text_search import segment_for_index


# revision identifiers, used by Alembic.
revision: str = '0020'
down_revision: Union[str, Sequence[str], None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# What revision 0008 created, frozen here so a downgrade restores that schema
# without depending on what ``app.db.fts`` says today.
_FTS_CREATE_ON_CONTENT: tuple[str, ...] = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts "
    "USING fts5(content, content='posts', content_rowid='id')",
    "CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN "
    "INSERT INTO posts_fts(rowid, content) VALUES (new.id, new.content); "
    "END",
    "CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN "
    "INSERT INTO posts_fts(posts_fts, rowid, content) "
    "VALUES ('delete', old.id, old.content); "
    "END",
    "CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN "
    "INSERT INTO posts_fts(posts_fts, rowid, content) "
    "VALUES ('delete', old.id, old.content); "
    "INSERT INTO posts_fts(rowid, content) VALUES (new.id, new.content); "
    "END",
    "INSERT INTO posts_fts(rowid, content) SELECT id, content FROM posts",
)

_BACKFILL_BATCH = 500


def _backfill_search_text(bind) -> None:
    """
    Fill ``search_text`` for every existing post.

    The segmentation is a Python function, so this reads the rows and writes
    them back in batches rather than expressing it as one UPDATE.
    """
    update = sa.text("UPDATE posts SET search_text = :search_text WHERE id = :id")
    rows = bind.execute(sa.text("SELECT id, content FROM posts")).all()
    for start in range(0, len(rows), _BACKFILL_BATCH):
        batch = rows[start : start + _BACKFILL_BATCH]
        bind.execute(
            update,
            [
                {"id": row.id, "search_text": segment_for_index(row.content or "")}
                for row in batch
            ],
        )


def upgrade() -> None:
    op.add_column(
        'posts',
        sa.Column(
            'search_text',
            sa.Text(),
            nullable=False,
            server_default='',
        ),
    )

    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        return

    # Drop the index first: its UPDATE trigger would otherwise fire once per
    # backfilled row, re-indexing text the index is about to be rebuilt from.
    for statement in SQLITE_FTS_DROP:
        op.execute(statement)

    _backfill_search_text(bind)

    for statement in SQLITE_FTS_CREATE:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        for statement in SQLITE_FTS_DROP:
            op.execute(statement)

    # Batch mode rebuilds ``posts`` to drop the column, which would take the FTS
    # triggers with it -- they are already gone above, and re-created below.
    with op.batch_alter_table('posts') as batch_op:
        batch_op.drop_column('search_text')

    if bind.dialect.name == 'sqlite':
        for statement in _FTS_CREATE_ON_CONTENT:
            op.execute(statement)
