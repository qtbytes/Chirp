"""
Set a user's password directly in a Chirp database.

There is no password-*reset* endpoint -- ``User`` carries no email, so there is
nowhere to send a token, and a forgotten password stays an operator job. (A
signed-in user can rotate their own via ``POST /auth/change-password``.) After
``prune_db.py --reset-passwords`` the kept accounts have a blank hash and cannot
log in at all; this script gives them a new one, using the same
``pbkdf2_sha256`` hashing the app uses.

Run it against the staged database before shipping:

    uv run --project backend python deploy/set_password.py --user dev

...or against the live database on the VPS:

    sudo -u chirp /srv/chirp/backend/.venv/bin/python deploy/set_password.py \
        --db /srv/chirp/backend/twitter.db --user dev

The password is read interactively by default so it never lands in your shell
history. ``--password`` exists for scripting; avoid it on a shared box.
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
DEFAULT_DB = REPO_ROOT / "deploy" / "out" / "twitter.db"

# Import the app's own hashing and password rules so neither the stored format
# nor the accepted length can drift from what the API does.
sys.path.insert(0, str(BACKEND))
from app.core.security import hash_password, verify_password  # noqa: E402
from app.schemas.user import (  # noqa: E402
    PASSWORD_MAX_LENGTH as MAX_PASSWORD_LENGTH,
    PASSWORD_MIN_LENGTH as MIN_PASSWORD_LENGTH,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--user", required=True, help="username to update")
    parser.add_argument(
        "--password",
        help="new password (omit to be prompted, which keeps it out of shell history)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"error: database not found: {args.db}")

    conn = sqlite3.connect(args.db)
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ?", (args.user,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"error: no user named {args.user!r} in {args.db}")

    user_id, existing = row
    state = "blank (login disabled)" if not existing else "set"
    print(f"{args.db}")
    print(f"user {args.user!r} (id={user_id}), current password: {state}")

    password = args.password
    if password is None:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Confirm: "):
            raise SystemExit("error: passwords do not match")

    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise SystemExit(
            f"error: password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} "
            "characters, to match what the API accepts"
        )

    new_hash = hash_password(password)
    if not verify_password(password, new_hash):  # paranoia; catches a broken build
        raise SystemExit("error: freshly generated hash does not verify")

    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id)
    )
    conn.commit()

    stored = conn.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()[0]
    conn.close()

    if not verify_password(password, stored):
        raise SystemExit("error: stored hash does not verify -- database not updated?")

    print(f"password updated for {args.user!r} and verified against the stored hash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
