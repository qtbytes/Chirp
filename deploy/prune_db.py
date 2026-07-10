"""
Build a production SQLite database from the local dev database, keeping only
the rows that belong to a chosen set of users.

The dev database accumulates throwaway accounts from manual testing. Shipping
it as-is would publish those accounts (and their password hashes, and their
email addresses) on the public site. This script copies the database and deletes
everything that does not belong to the users named with ``--keep``. It also
clears every ``email`` and ``pending_email``, and refuses to write a database
where one survived.

It also drops the legacy ``retweets`` table, which the quote-tweet refactor made
dead but which still lingers in older database files.

Usage (from the repo root):

    uv run python deploy/prune_db.py                       # dry-run report
    uv run python deploy/prune_db.py --apply               # write deploy/out/twitter.db
    uv run python deploy/prune_db.py --apply --reset-passwords

``--reset-passwords`` blanks the kept users' password hashes. Use it when the
old hashes have leaked: login then fails closed for those accounts until you
set a new password, rather than accepting the compromised one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "backend" / "twitter.db"
DEFAULT_DEST = REPO_ROOT / "deploy" / "out" / "twitter.db"
UPLOADS_DIR = REPO_ROOT / "backend" / "uploads"

LEGACY_TABLES = ("retweets", "comment_likes", "comment_retweets", "tweets", "comments")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def resolve_keep_ids(conn: sqlite3.Connection, usernames: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for name in usernames:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (name,)
        ).fetchone()
        if row is None:
            raise SystemExit(f"error: no user named {name!r} in the source database")
        found[name] = row["id"]
    return found


def strip_query(url: str) -> str:
    """``/uploads/avatars/1.jpg?v=123`` -> ``/uploads/avatars/1.jpg``."""
    return url.split("?", 1)[0]


def referenced_upload_paths(conn: sqlite3.Connection) -> set[str]:
    """Every /uploads/... path still referenced by a surviving row."""
    paths: set[str] = set()

    for row in conn.execute("SELECT avatar_url FROM users WHERE avatar_url IS NOT NULL"):
        paths.add(strip_query(row["avatar_url"]))

    for row in conn.execute("SELECT media_urls FROM posts WHERE media_urls IS NOT NULL"):
        try:
            urls = json.loads(row["media_urls"])
        except (TypeError, ValueError):
            continue
        if not isinstance(urls, list):
            continue
        for url in urls:
            if isinstance(url, str) and url.startswith("/uploads/"):
                paths.add(strip_query(url))

    return paths


def prune(conn: sqlite3.Connection, keep_ids: set[int], reset_passwords: bool) -> None:
    ids = ",".join(str(i) for i in sorted(keep_ids))

    # Posts survive only if their author survives.
    conn.execute(f"DELETE FROM posts WHERE user_id NOT IN ({ids})")

    # Everything else must point at a surviving user *and* a surviving post.
    conn.execute(f"DELETE FROM users WHERE id NOT IN ({ids})")
    conn.execute(
        f"DELETE FROM likes WHERE user_id NOT IN ({ids})"
        " OR post_id NOT IN (SELECT id FROM posts)"
    )
    conn.execute(
        f"DELETE FROM follows WHERE follower_id NOT IN ({ids})"
        f" OR followee_id NOT IN ({ids})"
    )
    conn.execute(
        f"DELETE FROM notifications WHERE user_id NOT IN ({ids})"
        f" OR actor_id NOT IN ({ids})"
        " OR (post_id IS NOT NULL AND post_id NOT IN (SELECT id FROM posts))"
    )
    conn.execute(
        f"DELETE FROM feed_items WHERE owner_id NOT IN ({ids})"
        f" OR actor_id NOT IN ({ids})"
        " OR post_id NOT IN (SELECT id FROM posts)"
    )

    # Self-references on posts: a surviving post must not point at a deleted one.
    for column in ("reply_to_id", "root_id", "quoted_post_id"):
        conn.execute(
            f"UPDATE posts SET {column} = NULL"
            f" WHERE {column} IS NOT NULL"
            f" AND {column} NOT IN (SELECT id FROM posts)"
        )

    for table in LEGACY_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")

    # Email addresses are personal data, and the surviving rows are dev/test
    # accounts whose addresses belong to whoever was testing. Shipping them would
    # publish those addresses and, worse, point password-reset mail at them from
    # the live site. The kept accounts get their passwords set by hand
    # (deploy/set_password.py); they do not need to reset anything.
    conn.execute("UPDATE users SET email = NULL, pending_email = NULL")

    if reset_passwords:
        # An empty hash cannot be produced by hash_password(), and
        # verify_password() returns False for it -- so login fails closed.
        conn.execute(f"UPDATE users SET password_hash = '' WHERE id IN ({ids})")


def verify(conn: sqlite3.Connection) -> list[str]:
    """Return a list of integrity problems; empty means the prune is consistent."""
    problems: list[str] = []

    for column in ("reply_to_id", "root_id", "quoted_post_id"):
        n = conn.execute(
            f"SELECT COUNT(*) FROM posts WHERE {column} IS NOT NULL"
            f" AND {column} NOT IN (SELECT id FROM posts)"
        ).fetchone()[0]
        if n:
            problems.append(f"{n} posts have a dangling {column}")

    n = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE user_id NOT IN (SELECT id FROM users)"
    ).fetchone()[0]
    if n:
        problems.append(f"{n} posts have no author")

    for table, column in (
        ("likes", "user_id"),
        ("likes", "post_id"),
        ("follows", "follower_id"),
        ("follows", "followee_id"),
        ("notifications", "user_id"),
        ("notifications", "actor_id"),
        ("feed_items", "owner_id"),
    ):
        parent = "posts" if column.endswith("post_id") else "users"
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} NOT IN (SELECT id FROM {parent})"
        ).fetchone()[0]
        if n:
            problems.append(f"{n} {table} rows have a dangling {column}")

    for table in LEGACY_TABLES:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if row:
            problems.append(f"legacy table {table!r} still present")

    leaked = conn.execute(
        "SELECT COUNT(*) FROM users WHERE email IS NOT NULL OR pending_email IS NOT NULL"
    ).fetchone()[0]
    if leaked:
        problems.append(f"{leaked} users still carry an email address")

    return problems


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ["users", "posts", "likes", "follows", "notifications", "feed_items"]
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument(
        "--keep",
        default="dev,test",
        help="comma-separated usernames to keep (default: dev,test)",
    )
    parser.add_argument("--apply", action="store_true", help="write the pruned database")
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="blank the kept users' password hashes so login fails closed",
    )
    parser.add_argument(
        "--copy-uploads",
        action="store_true",
        help="stage the still-referenced upload files under <dest dir>/uploads/",
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"error: source database not found: {args.source}")

    usernames = [n.strip() for n in args.keep.split(",") if n.strip()]

    with connect(args.source) as src:
        keep = resolve_keep_ids(src, usernames)
        before = counts(src)
        all_users = [
            (r["id"], r["username"]) for r in src.execute("SELECT id, username FROM users")
        ]

    keep_ids = set(keep.values())
    dropped = [f"{uid}:{name}" for uid, name in all_users if uid not in keep_ids]

    print(f"source:  {args.source}")
    print(f"dest:    {args.dest}")
    print(f"keeping: {', '.join(f'{n}(id={i})' for n, i in keep.items())}")
    print(f"dropping {len(dropped)} users: {', '.join(dropped) or '(none)'}")
    print(f"reset passwords: {args.reset_passwords}")
    print()

    if not args.apply:
        print("counts before:", before)
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        return 0

    args.dest.parent.mkdir(parents=True, exist_ok=True)

    # Copy rather than mutate the source, so the dev database keeps working --
    # but copy with the backup API, not shutil.copyfile.
    #
    # The dev database runs in WAL mode (see app/db/database.py), which means it
    # is three files and the newest rows live in twitter.db-wal until a
    # checkpoint. copyfile() takes only twitter.db, so it silently produced a
    # stale snapshot: the schema and rows written since the last checkpoint --
    # migrations included -- simply were not there. .backup() folds the WAL in.
    args.dest.unlink(missing_ok=True)
    source_conn = sqlite3.connect(args.source)
    dest_conn = sqlite3.connect(args.dest)
    with dest_conn:
        source_conn.backup(dest_conn)
    dest_conn.close()
    source_conn.close()

    with connect(args.dest) as dest:
        dest.execute("PRAGMA foreign_keys=OFF")
        prune(dest, keep_ids, args.reset_passwords)
        dest.commit()

        problems = verify(dest)
        after = counts(dest)
        kept_uploads = referenced_upload_paths(dest)

    if problems:
        print("INTEGRITY PROBLEMS -- refusing to ship this database:")
        for p in problems:
            print(f"  - {p}")
        args.dest.unlink(missing_ok=True)
        return 1

    # VACUUM must run outside a transaction.
    vac = sqlite3.connect(args.dest)
    vac.execute("VACUUM")
    vac.close()

    print("counts before:", before)
    print("counts after: ", after)
    print(f"\nintegrity: OK ({len(kept_uploads)} upload files still referenced)")

    if UPLOADS_DIR.is_dir():
        on_disk = {
            "/uploads/" + p.relative_to(UPLOADS_DIR).as_posix()
            for p in UPLOADS_DIR.rglob("*")
            if p.is_file()
        }
        orphans = sorted(on_disk - kept_uploads)
        missing = sorted(kept_uploads - on_disk)

        print(f"\nupload files to copy ({len(kept_uploads)}):")
        for path in sorted(kept_uploads):
            print(f"  keep    {path}")
        if orphans:
            print(f"\nupload files NOT referenced -- do not copy ({len(orphans)}):")
            for path in orphans:
                print(f"  orphan  {path}")
        if missing:
            print(f"\nWARNING: referenced but missing from disk ({len(missing)}):")
            for path in missing:
                print(f"  missing {path}")

        if args.copy_uploads:
            staged = args.dest.parent / "uploads"
            shutil.rmtree(staged, ignore_errors=True)
            copied = 0
            for path in sorted(kept_uploads):
                relative = path[len("/uploads/") :]
                source = UPLOADS_DIR / relative
                if not source.is_file():
                    continue
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
            print(f"\nstaged {copied} upload files under {staged}")

    print(f"\nwrote {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
