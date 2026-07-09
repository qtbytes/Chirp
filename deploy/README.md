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

## Bringing the dev/test data over

`twitter.db` and `uploads/` are gitignored, so they never travel through git.
Build a pruned database locally — only the `dev` and `test` users — and copy it
once.

> **A SQLite database in WAL mode is three files, not one.** Copying `twitter.db`
> on top of a running database leaves the old `twitter.db-wal` and
> `twitter.db-shm` beside it. On the next open, SQLite replays that stale log
> into the file you just copied and **silently overwrites it**. Always stop the
> services and delete all three files first.

```sh
# locally, from the repo root
uv run --project backend python deploy/prune_db.py --apply --copy-uploads --reset-passwords

# stop the app and clear the old database *and its WAL sidecars*
ssh root@vthe.shop 'systemctl stop chirp-api chirp-worker &&
    rm -f /srv/chirp/backend/twitter.db /srv/chirp/backend/twitter.db-wal \
          /srv/chirp/backend/twitter.db-shm'

scp deploy/out/twitter.db root@vthe.shop:/srv/chirp/backend/twitter.db
scp -r deploy/out/uploads/. root@vthe.shop:/srv/chirp/backend/uploads/

ssh root@vthe.shop '
    chown -R chirp:chirp /srv/chirp/backend
    chmod -R a+rX /srv/chirp/backend/uploads   # nginx (www-data) must read these
    sqlite3 /srv/chirp/backend/twitter.db "SELECT COUNT(*) || \" users\" FROM users"
    systemctl start chirp-api chirp-worker
'
```

That `SELECT COUNT(*)` should print `2 users`. If it prints `0`, a stale WAL
overwrote the copy — repeat with the services stopped and all three files removed.
`deploy.sh` prints the same count after every deploy for this reason.

Run `prune_db.py` with no flags first for a dry-run report. Add
`--reset-passwords` to blank the two password hashes — those hashes were public
on GitHub, so unless you have rotated the passwords you want this.

### Setting a password after `--reset-passwords`

A blank hash rejects every password, and there is no reset endpoint, so the
accounts cannot log in until you give them one. Do it on the staged database
before shipping:

```sh
uv run --project backend python deploy/set_password.py --user dev
uv run --project backend python deploy/set_password.py --user test
```

Or on the live database, after deploying:

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

Idempotent. It restarts the services and leaves `twitter.db`, `uploads/`, and the
existing `SESSION_SECRET_KEY` untouched.

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
