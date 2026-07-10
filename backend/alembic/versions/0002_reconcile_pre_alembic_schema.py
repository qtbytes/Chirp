"""reconcile pre-alembic schema

Bring databases created before Alembic in line with revision 0001.

``posts``, ``likes`` and ``notifications`` were originally created by hand-written
SQL, so ``create_all()`` -- which skips tables that already exist, indexes
included -- never brought them up to what the models declare. Against the dev and
pruned production databases, ``alembic check`` reported:

- ``ix_posts_created_at`` and ``ix_posts_user_id`` missing entirely, though
  ``Post`` declares ``index=True`` on both columns. ``list_for_you_tweets``
  orders every row by ``created_at`` and seeks on it for cursor pagination;
  profile timelines filter by ``user_id``.
- ``posts.created_at`` and ``likes.created_at`` typed ``TIMESTAMP`` rather than
  ``DateTime``, and ``posts.id`` / ``notifications.id`` reflecting as nullable
  (raw ``id INTEGER PRIMARY KEY`` omits ``NOT NULL``).
- ``posts.quoted_post_id`` carrying no foreign key.

Only the missing indexes change behaviour -- on SQLite the rest is cosmetic,
since TIMESTAMP and DATETIME share NUMERIC affinity, an INTEGER PRIMARY KEY is a
rowid alias that cannot be null, and foreign keys are deliberately left off (see
``app/db/database.py``). They are still worth erasing: left in place, the next
``alembic revision --autogenerate`` run against a developer's database would
silently fold all of it into an unrelated migration.

SQLite cannot ALTER any of this in place, so the three tables are rebuilt via
``batch_alter_table`` -- create a new table from the definition below, copy the
rows across, drop the original, rename. The rebuild is unconditional because it
is idempotent by construction: it always produces the schema spelled out here,
whether the table it replaces was drifted or already correct. On a database
freshly created by 0001 it rewrites three tables that are still empty.

Also drops ``retweets``, which the quote-tweet refactor abandoned -- a retweet is
now an ordinary post with ``quoted_post_id`` set. No model, query or route reads
it, ``deploy/prune_db.py`` already lists it as legacy, and it held no rows in
either database.

These definitions are copied out rather than imported from ``app.models`` on
purpose: a migration must keep describing the schema of its own moment in time,
even after the models move on.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _posts_table() -> sa.Table:
    metadata = sa.MetaData()
    table = sa.Table(
        "posts",
        metadata,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("media_urls", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_to_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column("root_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column("quoted_post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Index("ix_posts_created_at", table.c.created_at)
    sa.Index("ix_posts_user_id", table.c.user_id)
    sa.Index("ix_posts_user_created", table.c.user_id, table.c.created_at)
    sa.Index("ix_posts_root_created", table.c.root_id, table.c.created_at)
    sa.Index("ix_posts_reply_created", table.c.reply_to_id, table.c.created_at)
    return table


def _likes_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "likes",
        metadata,
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "post_id", name="pk_likes"),
    )


def _notifications_table() -> sa.Table:
    metadata = sa.MetaData()
    table = sa.Table(
        "notifications",
        metadata,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    sa.Index("ix_notifications_created_at", table.c.created_at)
    sa.Index("ix_notifications_user_id", table.c.user_id)
    sa.Index("ix_notifications_user_created", table.c.user_id, table.c.created_at)
    return table


def upgrade() -> None:
    # Rebuild posts first: likes and notifications reference it, and dropping the
    # original is only safe while SQLite foreign keys are off -- which they are.
    for table in (_posts_table(), _likes_table(), _notifications_table()):
        with op.batch_alter_table(table.name, copy_from=table, recreate="always"):
            pass

    if "retweets" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("retweets")


def downgrade() -> None:
    """
    Deliberately empty.

    Revision 0001 declares these tables exactly as they are rebuilt above, and
    does not declare ``retweets``. Downgrading to it should leave precisely what
    this revision produced, so there is nothing to undo.
    """
