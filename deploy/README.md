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
once:

```sh
# locally, from the repo root
uv run --project backend python deploy/prune_db.py --apply --copy-uploads
scp deploy/out/twitter.db root@vthe.shop:/srv/chirp/backend/twitter.db
scp -r deploy/out/uploads/. root@vthe.shop:/srv/chirp/backend/uploads/
ssh root@vthe.shop 'chown -R chirp:chirp /srv/chirp/backend && systemctl restart chirp-api'
```

Run `prune_db.py` with no flags first for a dry-run report. Add
`--reset-passwords` to blank the two password hashes — those hashes were public
on GitHub, so unless you have rotated the passwords you want this.

## Redeploy

```sh
ssh root@vthe.shop 'cd /srv/chirp && git pull && ./deploy/deploy.sh'
```

Idempotent. It rebuilds, restarts, and leaves `twitter.db`, `uploads/`, and the
existing `SESSION_SECRET_KEY` untouched.

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
