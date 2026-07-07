"""
Backfill migration: unify ``tweets`` + ``comments`` into a single ``posts`` table.

This is the data half of the "everything is a Post" refactor. It is a **cutover**
migration: run ``--dry-run`` (the default) as many times as you like to validate
counts and detect problems, then run ``--apply`` once, together with deploying the
matching code changes (repositories/routes reading ``posts``). URLs stay the same;
only the storage changes.

What it does (all inside one transaction on ``--apply``):

1. Creates ``posts(id, user_id, content, media_urls, created_at, reply_to_id,
   root_id)``.
     - Every tweet becomes a top-level post: ``reply_to_id = NULL``,
       ``root_id = id`` (id preserved).
     - Every comment becomes a reply post with a shifted id (``id + OFFSET`` where
       ``OFFSET = MAX(tweets.id)``) so tweet and comment id spaces never collide:
         * ``reply_to_id`` = the tweet id (top-level comment) or
           ``parent_comment_id + OFFSET`` (nested reply)
         * ``root_id`` = the comment's ``tweet_id`` (always the thread origin —
           enforced by create_comment, verified before writing)
2. Rebuilds ``likes`` and ``retweets`` with a ``post_id`` column, merging in the
   ``comment_likes`` / ``comment_retweets`` rows (comment ids shifted by OFFSET).
3. Adds ``notifications.post_id`` = ``COALESCE(comment_id + OFFSET, tweet_id)`` and
   folds the ``comment_like`` -> ``like`` and ``comment_retweet`` -> ``retweet``
   types (there is no tweet/comment distinction anymore).

It does **not** drop ``tweets`` / ``comments`` / ``comment_likes`` /
``comment_retweets`` — those stay for rollback and are removed in a later step once
the new code has been verified. The whole DB file is also backed up first.

Usage (from the backend directory):

    uv run python scripts/migrate_to_posts.py              # dry-run report
    uv run python scripts/migrate_to_posts.py --apply      # perform the migration

The migration is a one-shot cutover, not idempotent: applying it rebuilds ``likes``
and ``retweets`` in place, so it cannot be run twice. To redo it, restore the
``.bak-*`` copy it makes (or reset the dev database) and apply again.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the ``app`` package importable when run as ``python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import Connection, Engine  # noqa: E402

from app.core.config import settings  # noqa: E402

SOURCE_TABLES = [
    "tweets",
    "comments",
    "likes",
    "comment_likes",
    "retweets",
    "comment_retweets",
    "notifications",
]


def _scalar(conn: Connection, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def _count(conn: Connection, table: str) -> int:
    return _scalar(conn, f"SELECT COUNT(*) FROM {table}")


def gather_report(conn: Connection, offset: int) -> dict[str, int]:
    """Read-only counts used by both the dry-run report and the post-apply check."""
    n_tweets = _count(conn, "tweets")
    n_comments = _count(conn, "comments")
    n_likes = _count(conn, "likes")
    n_comment_likes = _count(conn, "comment_likes")
    n_retweets = _count(conn, "retweets")
    n_comment_retweets = _count(conn, "comment_retweets")
    n_notifications = _count(conn, "notifications")

    # Rows that would be dropped because their target no longer exists.
    orphan_comment_likes = _scalar(
        conn,
        "SELECT COUNT(*) FROM comment_likes cl "
        "LEFT JOIN comments c ON c.id = cl.comment_id WHERE c.id IS NULL",
    )
    orphan_comment_retweets = _scalar(
        conn,
        "SELECT COUNT(*) FROM comment_retweets cr "
        "LEFT JOIN comments c ON c.id = cr.comment_id WHERE c.id IS NULL",
    )
    # Comments whose parent tweet is gone (would become orphan posts).
    orphan_comments = _scalar(
        conn,
        "SELECT COUNT(*) FROM comments c "
        "LEFT JOIN tweets t ON t.id = c.tweet_id WHERE t.id IS NULL",
    )
    # Sanity: a comment's tweet_id must equal its root (create_comment enforces
    # this, but verify before we rely on root_id = tweet_id).
    bad_roots = _scalar(
        conn,
        "SELECT COUNT(*) FROM comments c "
        "JOIN comments p ON p.id = c.parent_comment_id "
        "WHERE c.tweet_id != p.tweet_id",
    )

    return {
        "offset": offset,
        "tweets": n_tweets,
        "comments": n_comments,
        "posts_after": n_tweets + n_comments,
        "likes": n_likes,
        "comment_likes": n_comment_likes,
        "likes_after": n_likes + n_comment_likes,
        "retweets": n_retweets,
        "comment_retweets": n_comment_retweets,
        "retweets_after": n_retweets + n_comment_retweets,
        "notifications": n_notifications,
        "orphan_comments": orphan_comments,
        "orphan_comment_likes": orphan_comment_likes,
        "orphan_comment_retweets": orphan_comment_retweets,
        "bad_roots": bad_roots,
    }


def print_report(report: dict[str, int]) -> None:
    print("\n=== migrate_to_posts: plan ===")
    print(f"  id offset for comments : +{report['offset']}")
    print(f"  tweets   -> posts      : {report['tweets']:>7}")
    print(f"  comments -> posts      : {report['comments']:>7}")
    print(f"  posts total (after)    : {report['posts_after']:>7}")
    print("  --")
    print(f"  likes  + comment_likes : {report['likes']} + {report['comment_likes']}"
          f" = {report['likes_after']}")
    print(f"  retweets + c_retweets  : {report['retweets']} + "
          f"{report['comment_retweets']} = {report['retweets_after']}")
    print(f"  notifications remapped : {report['notifications']:>7}")

    warnings = []
    if report["orphan_comments"]:
        warnings.append(
            f"{report['orphan_comments']} comment(s) have no parent tweet "
            "(their posts would be orphaned)"
        )
    if report["orphan_comment_likes"]:
        warnings.append(
            f"{report['orphan_comment_likes']} comment_like(s) point at a missing "
            "comment (will be dropped)"
        )
    if report["orphan_comment_retweets"]:
        warnings.append(
            f"{report['orphan_comment_retweets']} comment_retweet(s) point at a "
            "missing comment (will be dropped)"
        )
    if report["bad_roots"]:
        warnings.append(
            f"CRITICAL: {report['bad_roots']} nested repl(y/ies) have tweet_id != "
            "root tweet_id — root_id = tweet_id assumption is violated"
        )

    if warnings:
        print("\n  warnings:")
        for line in warnings:
            print(f"    - {line}")
    else:
        print("\n  no anomalies detected.")


def backup_sqlite(engine: Engine) -> str | None:
    """Copy the SQLite file next to the original before mutating it."""
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


def apply_migration(conn: Connection, offset: int) -> None:
    # 1. posts table + indexes -------------------------------------------------
    conn.execute(
        text(
            """
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                content TEXT NOT NULL,
                media_urls JSON,
                created_at TIMESTAMP NOT NULL,
                reply_to_id INTEGER REFERENCES posts(id),
                root_id INTEGER REFERENCES posts(id)
            )
            """
        )
    )
    # Tweets -> top-level posts.
    conn.execute(
        text(
            """
            INSERT INTO posts (id, user_id, content, media_urls, created_at,
                               reply_to_id, root_id)
            SELECT id, user_id, content, media_urls, created_at, NULL, id
            FROM tweets
            """
        )
    )
    # Comments -> reply posts (id shifted; only those with a surviving tweet).
    conn.execute(
        text(
            """
            INSERT INTO posts (id, user_id, content, media_urls, created_at,
                               reply_to_id, root_id)
            SELECT c.id + :off, c.user_id, c.content, c.media_urls, c.created_at,
                   CASE WHEN c.parent_comment_id IS NULL
                        THEN c.tweet_id
                        ELSE c.parent_comment_id + :off END,
                   c.tweet_id
            FROM comments c
            JOIN tweets t ON t.id = c.tweet_id
            """
        ),
        {"off": offset},
    )
    conn.execute(text("CREATE INDEX ix_posts_user_created ON posts (user_id, created_at)"))
    conn.execute(text("CREATE INDEX ix_posts_root_created ON posts (root_id, created_at)"))
    conn.execute(text("CREATE INDEX ix_posts_reply_created ON posts (reply_to_id, created_at)"))

    # 2. rebuild likes / retweets with post_id --------------------------------
    _rebuild_engagement(conn, "likes", "comment_likes", offset)
    _rebuild_engagement(conn, "retweets", "comment_retweets", offset)

    # 3. notifications: add post_id, remap types ------------------------------
    cols = {c["name"] for c in inspect(conn).get_columns("notifications")}
    if "post_id" not in cols:
        conn.execute(text("ALTER TABLE notifications ADD COLUMN post_id INTEGER"))
    conn.execute(
        text(
            "UPDATE notifications "
            "SET post_id = CASE WHEN comment_id IS NOT NULL "
            "THEN comment_id + :off ELSE tweet_id END"
        ),
        {"off": offset},
    )
    conn.execute(text("UPDATE notifications SET type = 'like' WHERE type = 'comment_like'"))
    conn.execute(
        text("UPDATE notifications SET type = 'retweet' WHERE type = 'comment_retweet'")
    )


def _rebuild_engagement(
    conn: Connection, base_table: str, comment_table: str, offset: int
) -> None:
    """Recreate ``likes``/``retweets`` keyed by post_id, merging the comment_* rows."""
    comment_col = "comment_id"
    conn.execute(
        text(
            f"""
            CREATE TABLE {base_table}_new (
                user_id INTEGER NOT NULL REFERENCES users(id),
                post_id INTEGER NOT NULL REFERENCES posts(id),
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (user_id, post_id)
            )
            """
        )
    )
    # Existing tweet-level rows: tweet_id is already a valid post id.
    conn.execute(
        text(
            f"INSERT OR IGNORE INTO {base_table}_new (user_id, post_id, created_at) "
            f"SELECT user_id, tweet_id, created_at FROM {base_table}"
        )
    )
    # Comment-level rows: shift the comment id, drop any pointing at a gone comment.
    conn.execute(
        text(
            f"INSERT OR IGNORE INTO {base_table}_new (user_id, post_id, created_at) "
            f"SELECT ct.user_id, ct.{comment_col} + :off, ct.created_at "
            f"FROM {comment_table} ct "
            f"JOIN posts p ON p.id = ct.{comment_col} + :off"
        ),
        {"off": offset},
    )
    conn.execute(text(f"DROP TABLE {base_table}"))
    conn.execute(text(f"ALTER TABLE {base_table}_new RENAME TO {base_table}"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration (default is a read-only dry run)",
    )
    args = parser.parse_args()

    engine = create_engine(settings.database_url)
    print(f"database: {engine.url}")

    with engine.connect() as conn:
        existing = set(inspect(conn).get_table_names())
        missing = [t for t in SOURCE_TABLES if t not in existing]
        if missing:
            print(f"ERROR: source tables missing: {', '.join(missing)}")
            return 1

        if "posts" in existing:
            print(
                "ERROR: a 'posts' table already exists — this migration has already "
                "run (it is a one-shot cutover). To redo it, restore the .bak-* copy "
                "it created (or reset the dev database), then apply again."
            )
            return 1

        offset = _scalar(conn, "SELECT COALESCE(MAX(id), 0) FROM tweets")
        report = gather_report(conn, offset)
        print_report(report)

        if report["bad_roots"]:
            print(
                "\nABORTING: the root_id = tweet_id assumption is violated; fix the "
                "data before migrating."
            )
            return 1

        if not args.apply:
            print("\ndry run only — no changes written. Re-run with --apply to migrate.")
            return 0

    # --apply: mutate inside one transaction, with a file backup first.
    print("\napplying migration...")
    backup_sqlite(engine)
    with engine.begin() as conn:
        apply_migration(conn, offset)

        posts_after = _count(conn, "posts")
        expected_posts = report["tweets"] + report["comments"] - report["orphan_comments"]
        if posts_after != expected_posts:
            raise RuntimeError(
                f"post count mismatch: got {posts_after}, expected {expected_posts} "
                "(transaction rolled back)"
            )
        print(f"  posts created          : {posts_after}")
        print(f"  likes (merged)         : {_count(conn, 'likes')}")
        print(f"  retweets (merged)      : {_count(conn, 'retweets')}")

    print("\ndone. Old tweets/comments/comment_* tables were left intact for rollback.")
    print("Deploy the code changes that read 'posts', verify, then drop the old tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
