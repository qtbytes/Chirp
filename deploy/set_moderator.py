"""
Grant or revoke a user's moderator flag directly in a Chirp database.

There is deliberately no API for this: ``users.is_moderator`` gates the
moderation queue, and letting any signed-in surface flip it would make the
gate self-serving. Like set_password.py, it is an operator action performed
on the server.

Run it against the local dev database:

    uv run --project backend python deploy/set_moderator.py --user dev

...or against the live database on the VPS:

    sudo -u chirp /srv/chirp/backend/.venv/bin/python deploy/set_moderator.py \
        --db /srv/chirp/backend/twitter.db --user dev

``--revoke`` removes the flag instead of granting it.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "backend" / "twitter.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--user", required=True, help="username to update")
    parser.add_argument(
        "--revoke", action="store_true", help="remove the flag instead of granting it"
    )
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"error: database not found: {args.db}")

    conn = sqlite3.connect(args.db)
    row = conn.execute(
        "SELECT id, is_moderator, deleted_at FROM users WHERE username = ?",
        (args.user,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"error: no user named {args.user!r} in {args.db}")

    user_id, is_moderator, deleted_at = row
    if deleted_at is not None:
        raise SystemExit(f"error: {args.user!r} is a deleted account")

    grant = not args.revoke
    if bool(is_moderator) == grant:
        state = "already a moderator" if grant else "not a moderator"
        print(f"user {args.user!r} (id={user_id}) is {state}; nothing to do")
        conn.close()
        return 0

    conn.execute(
        "UPDATE users SET is_moderator = ? WHERE id = ?", (1 if grant else 0, user_id)
    )
    conn.commit()
    conn.close()

    action = "granted to" if grant else "revoked from"
    print(f"moderator flag {action} {args.user!r} (id={user_id}) in {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
