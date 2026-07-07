"""
Finish the tweets+comments -> posts cutover by dropping the legacy tables.

``migrate_to_posts.py`` intentionally left the old ``tweets``, ``comments``,
``comment_likes`` and ``comment_retweets`` tables in place (plus the now-unused
``tweet_id`` / ``comment_id`` columns on ``notifications``) so the migration
could be rolled back. Once the new ``posts``-based code has been verified in the
running app, this script removes them.

It refuses to run unless the migration clearly completed (a ``posts`` table
exists and ``likes`` / ``retweets`` / ``notifications`` carry ``post_id``), and
it sanity-checks that ``posts`` holds at least as many rows as ``tweets`` before
deleting anything. Run ``--dry-run`` (the default) to preview, then ``--apply``.

Usage (from the backend directory):

    uv run python scripts/drop_legacy_tables.py            # dry-run report
    uv run python scripts/drop_legacy_tables.py --apply    # drop the legacy tables
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import Connection, Engine  # noqa: E402

from app.core.config import settings  # noqa: E402

LEGACY_TABLES = ["comment_retweets", "comment_likes", "comments", "tweets"]
LEGACY_NOTIFICATION_COLUMNS = ["tweet_id", "comment_id"]


def _count(conn: Connection, table: str) -> int:
    return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def _columns(conn: Connection, table: str) -> set[str]:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def migration_is_complete(conn: Connection, tables: set[str]) -> tuple[bool, str]:
    """Confirm the posts migration ran before we delete anything."""
    if "posts" not in tables:
        return False, "no 'posts' table — run migrate_to_posts.py --apply first"
    for engagement in ("likes", "retweets"):
        if engagement in tables and "post_id" not in _columns(conn, engagement):
            return False, f"'{engagement}' has no post_id column — migration incomplete"
    if "notifications" in tables and "post_id" not in _columns(conn, "notifications"):
        return False, "'notifications' has no post_id column — migration incomplete"
    if "tweets" in tables and _count(conn, "posts") < _count(conn, "tweets"):
        return False, "posts has fewer rows than tweets — refusing to drop legacy data"
    return True, ""


def rebuild_notifications_without_legacy_columns(conn: Connection) -> None:
    """
    Recreate ``notifications`` without the vestigial ``tweet_id`` / ``comment_id``
    columns.

    SQLite cannot ``DROP COLUMN`` a column that participates in a foreign key
    (both legacy columns reference the tables we are about to drop), so the
    standard create-new / copy / drop / rename rebuild is used instead.
    """
    conn.execute(
        text(
            """
            CREATE TABLE notifications_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                actor_id INTEGER NOT NULL REFERENCES users(id),
                type VARCHAR(32) NOT NULL,
                post_id INTEGER REFERENCES posts(id),
                is_read BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            "INSERT INTO notifications_new "
            "(id, user_id, actor_id, type, post_id, is_read, created_at) "
            "SELECT id, user_id, actor_id, type, post_id, is_read, created_at "
            "FROM notifications"
        )
    )
    conn.execute(text("DROP TABLE notifications"))
    conn.execute(text("ALTER TABLE notifications_new RENAME TO notifications"))
    conn.execute(
        text("CREATE INDEX ix_notifications_user_created ON notifications (user_id, created_at)")
    )
    conn.execute(text("CREATE INDEX ix_notifications_user_id ON notifications (user_id)"))
    conn.execute(text("CREATE INDEX ix_notifications_created_at ON notifications (created_at)"))


def backup_sqlite(engine: Engine) -> str | None:
    if not engine.url.drivername.startswith("sqlite"):
        print("  (non-sqlite database: skipping file backup — back up manually!)")
        return None
    db_path = engine.url.database
    if not db_path or db_path == ":memory:":
        return None
    src = Path(db_path)
    if not src.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(f"{src.name}.bak-{stamp}")
    shutil.copy2(src, dst)
    print(f"  backed up database to: {dst}")
    return str(dst)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="drop the legacy tables (default is a read-only dry run)",
    )
    args = parser.parse_args()

    engine = create_engine(settings.database_url)
    print(f"database: {engine.url}")

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())

        ok, reason = migration_is_complete(conn, tables)
        if not ok:
            print(f"ERROR: {reason}")
            return 1

        present = [t for t in LEGACY_TABLES if t in tables]
        vestigial_cols = (
            [c for c in LEGACY_NOTIFICATION_COLUMNS if c in _columns(conn, "notifications")]
            if "notifications" in tables
            else []
        )

        print("\n=== drop_legacy_tables: plan ===")
        if present:
            for table in present:
                print(f"  DROP TABLE {table:<18} ({_count(conn, table)} rows)")
        if vestigial_cols:
            for col in vestigial_cols:
                print(f"  DROP COLUMN notifications.{col}")
        if not present and not vestigial_cols:
            print("  nothing to drop — already clean.")
            return 0

        if not args.apply:
            print("\ndry run only — no changes written. Re-run with --apply to drop.")
            return 0

    print("\napplying...")
    backup_sqlite(engine)
    with engine.begin() as conn:
        # Rebuild notifications first so it no longer carries foreign keys into
        # the tables we are about to drop, then drop the legacy tables.
        if vestigial_cols:
            rebuild_notifications_without_legacy_columns(conn)
        for table in present:
            conn.execute(text(f"DROP TABLE {table}"))

    print(f"\ndone. Dropped {len(present)} table(s)"
          + (f" and rebuilt notifications without {len(vestigial_cols)} vestigial "
             "column(s)." if vestigial_cols else "."))
    print("The cutover is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
