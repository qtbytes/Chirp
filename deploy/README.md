# Deploy

One config file, one script. The repo is checked out on the VPS and the app runs
from that checkout, so `git pull` *is* the file sync.

nginx serves the built frontend and `/uploads/` from disk and proxies `/api/` to
uvicorn on loopback. Everything is same-origin.

## First deploy

```sh
ssh root@vthe.shop
# HTTPS, not git@github.com: the server only needs anonymous read access, and
# SSH would require a key on the box. Use SSH only if the repo becomes private.
git clone https://github.com/qtbytes/Chirp.git /srv/chirp
cd /srv/chirp
cp deploy/deploy.conf.example deploy/deploy.conf
$EDITOR deploy/deploy.conf          # set DOMAIN, at minimum
./deploy/deploy.sh
```

The script installs nginx/redis/node/uv, creates the `chirp` user, generates
`backend/.env` with a random `SESSION_SECRET_KEY`, builds the frontend, installs
the systemd units, writes the nginx site, and health-checks the result.

It has no TLS certificate on the first run, so it writes an HTTP-only site.
Get a cert, then re-run to switch to HTTPS:

```sh
apt install -y certbot python3-certbot-nginx
certbot --nginx -d vthe.shop -d www.vthe.shop
./deploy/deploy.sh
```

`DOMAIN` must not already be served by another file in `/etc/nginx/sites-enabled/`.
nginx keeps the first `server` block it loads for a given `server_name` and
ignores later duplicates with only a warning, so reusing a name silently takes
the *other* site offline. `deploy.sh` checks for this and refuses.

To hand the domain to Chirp, disable the other site first. Removing the symlink
leaves `sites-available/` intact, so it is one command to undo:

```sh
rm /etc/nginx/sites-enabled/<other-site>
nginx -t && systemctl reload nginx
```

> Until TLS is up the site is HTTP, and `SESSION_COOKIE_SECURE=true` means the
> browser will refuse to store the login cookie. Log in only after certbot runs.

## Setting a password

`deploy/set_password.py` writes a user's password hash directly into a
database, using the same `pbkdf2_sha256` hashing the app does — the operator
path for an account that cannot use the normal flows (a blanked hash, no
confirmed email):

```sh
cd /srv/chirp
sudo -u chirp backend/.venv/bin/python deploy/set_password.py \
    --db backend/twitter.db --user dev
systemctl restart chirp-api      # only needed if the app is already running
```

It prompts for the password (so it stays out of your shell history), enforces the
same 8–128 character rule the API does, and verifies the stored hash before
exiting. `--password` exists for scripting; avoid it on a shared box.

## Redeploy

```sh
ssh root@vthe.shop 'cd /srv/chirp && git pull && ./deploy/deploy.sh'
```

Idempotent. It leaves `uploads/` and the existing `SESSION_SECRET_KEY` untouched.
Before restarting the services it snapshots `twitter.db` to
`twitter.db.bak-<timestamp>` (through the backup API, which folds the WAL into one
consistent file) and runs `alembic upgrade head` — migrations apply while the old
code is still serving, then the new code starts against the new schema.

The two slow steps are cached, so a backend-only or docs-only deploy skips both:

- **`npm ci`** runs only when `package.json` / `package-lock.json` change. (It
  deletes `node_modules` and reinstalls from scratch, so it is never cheap.)
- **`vite build`** runs only when a file under `frontend/` changes, or when
  `DOMAIN` changes — the domain is baked into the bundle as `VITE_API_BASE_URL`.

Fingerprints live in `/srv/chirp/.deploy/`. To rebuild regardless:

```sh
FORCE_BUILD=1 ./deploy/deploy.sh
```

## Backups

SQLite runs in WAL mode, so copy it with the backup API, not `cp`:

```sh
sudo -u chirp sqlite3 /srv/chirp/backend/twitter.db ".backup '/srv/chirp/backup-$(date +%F).db'"
tar czf /srv/chirp/uploads-$(date +%F).tar.gz -C /srv/chirp/backend uploads
```

> **A WAL-mode database is three files, not one.** Copying `twitter.db` on top
> of a running database leaves the old `twitter.db-wal` and `twitter.db-shm`
> beside it; on the next open, SQLite replays that stale log into the file you
> just copied and **silently overwrites it**. When restoring a copy, stop the
> services and delete all three files first. The backup API folds the WAL into
> one consistent file — which is why this command and `deploy.sh`'s
> pre-migration snapshot both use it, and why `deploy.sh` prints the user
> count after every deploy: a database clobbered by a stale WAL looks like a
> perfectly healthy empty site.

## Schema migrations

Alembic owns the schema. `deploy.sh` runs `alembic upgrade head` on every deploy,
so there is normally nothing to do by hand. To inspect or drive it yourself:

```sh
cd /srv/chirp/backend
sudo -u chirp .venv/bin/alembic current      # applied revision
sudo -u chirp .venv/bin/alembic history      # what exists
sudo -u chirp .venv/bin/alembic upgrade head
```

Run it from `backend/`: `alembic/env.py` reads `DATABASE_URL` out of `backend/.env`
through the app's own `Settings`, so it can never target a different file than the
API does.

If a migration fails, the deploy aborts before the services restart, and the
snapshot it just took is sitting next to the database:

```sh
systemctl stop chirp-api chirp-worker
cd /srv/chirp/backend
rm -f twitter.db twitter.db-wal twitter.db-shm    # WAL sidecars, see above
cp twitter.db.bak-<timestamp> twitter.db
chown chirp:chirp twitter.db
systemctl start chirp-api chirp-worker
```

## Outbound mail

Email confirmation and password reset need SMTP. Set `SMTP_HOST` (and usually
`SMTP_USERNAME` / `SMTP_PASSWORD`) in `deploy/deploy.conf`; `deploy.sh` copies
them into `backend/.env`. See `deploy.conf.example`.

Leave `SMTP_HOST` unset and the site still works — people register, log in, post.
They just never receive a confirmation link, so nobody can reset a forgotten
password, and `deploy.sh` warns about it on every run.

**The app will not print mail to the log in production.** With
`SESSION_COOKIE_SECURE=true` and no `SMTP_HOST`, `send_email` raises rather than
fall back to the console sender it uses locally — a reset token in `journalctl`
is a credential sitting in a file half the box can read. `POST /auth/change-email`
and `/auth/resend-verification` then answer 503.

`POST /auth/forgot-password` is the exception: it answers **202 regardless**,
even when the mailer is down, and logs the failure instead. It only ever reaches
the mailer for an address that exists, so a 503 would tell an attacker exactly
what the uniform 202 exists to hide. Watch the log, not the status code:

```sh
journalctl -u chirp-api | grep 'could not send'
```

## Things worth knowing

**Redis is mandatory.** The timeline and link-preview caches degrade gracefully
without it, but sessions and rate limiting do not: auth returns **503** and the
limiter returns **503** rather than failing open. Setting
`RATE_LIMIT_ENABLED=false` removes the limiter's dependency; nothing removes the
session store's, by design — silently falling back to per-worker in-process
sessions would be worse than an outage.

**One uvicorn worker, on purpose.** The API and the RQ worker already share a
single SQLite file. WAL plus a 5s busy timeout (`app/db/database.py`) makes two
writers safe; more workers would only add contention. Move to Postgres before
scaling — only `DATABASE_URL` and that pragma listener are SQLite-specific.

**Stopping `chirp-worker` is safe.** Fan-out then runs inline in the request.

**Sessions live in Redis.** The cookie carries an opaque, HMAC-signed session id;
Redis maps it to a user id with a sliding 14-day idle TTL (`SESSION_TTL_SECONDS`).
Logout deletes the record, so a captured cookie stops working immediately, and
`revoke_user_sessions(user_id)` logs a user out everywhere.

The script enables Redis AOF persistence for this reason — without it, a Redis
restart logs everyone out. Rotating `SESSION_SECRET_KEY` still invalidates every
session at once; to drop one session, delete its `session:<id>` key.
