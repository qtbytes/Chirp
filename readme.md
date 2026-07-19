# Chirp

A full-stack Twitter clone built to explore the system-design questions behind a
social timeline — fan-out strategies, cursor pagination, caching, and background
work — with a real UI on top rather than a toy script.

- **Backend** — FastAPI + SQLAlchemy 2.0 + Pydantic v2, SQLite by default, Redis
  and an RQ worker when available.
- **Frontend** — React 19 + TypeScript + Vite, React Router, no CSS framework.

## Features

Posting with images and video, threaded comments and replies, likes, quote
tweets, follows, profiles, full-text search with hashtags and mentions, direct
messages, blocking and muting, reporting with a moderation queue (operator-
granted moderators; reversible takedowns and account suspensions; judgement
notifications), per-tweet audience controls, view counts, email
confirmation, password change and reset, active session management, live
notifications over SSE, emoji picker, and Open Graph link preview cards.

## Design highlights

Full write-ups in [`docs/design-notes.md`](docs/design-notes.md).

- **One `Post` model for everything.** Tweets, comments, and replies are the
  same row (`reply_to_id`, `root_id`). A quote tweet is a post with
  `quoted_post_id`; a retweet is a quote with empty content — no join table.
- **Two timeline strategies behind one API.** `?strategy=read` queries followed
  users at request time; `?strategy=write` fans out to `feed_items` via an RQ
  worker. Everything paginates by `created_at|id` cursor, never offset.
- **The "for you" feed is ranked at read time** — `(base + engagement) ×
  time-decay × affinity` over a bounded candidate pool. Its cursor freezes the
  reference clock so every page of one scroll decays against the same "now".
- **Redis fails closed only where it must.** Sessions and rate limiting answer
  503 without it; timeline/link-preview caches just miss, and fan-out falls
  back to inline execution.
- **SSE notifications are a nudge, not a payload.** Published on
  `after_commit`, and the client re-reads the authoritative unread count, so
  the badge can never drift. Notifications dedupe on
  `(recipient, actor, type, post_id)`, so toggling a like can't spam.
- **Sessions are server-side and revocable.** An opaque HMAC-signed cookie id
  maps to the user in Redis. Password change/reset revokes every session
  *before* writing the new hash; sessions are listable and individually
  revocable.
- **A claimed email is not a confirmed one.** Only a redeemed token promotes
  `pending_email` to the uniquely-indexed `email`; reset mail goes only to
  confirmed addresses, and `forgot-password` always answers 202. Mailed tokens
  are stored hashed and are single-use.
- **Rate-limit buckets are named by config key** and read from `Settings` per
  request. Writes bucket by user; login/register bucket by IP.
- **Blocking is symmetric** — one read filter (`hidden_user_ids`) threaded into
  every list path, one write guard in the repository layer. A blocked
  interaction answers 404, never 403.
- **Alembic owns the schema.** `alembic upgrade head` is the one command
  everywhere, and `alembic check` runs in the test suite so models and
  migrations cannot drift apart.
- **Link previews unfurl any URL** via oEmbed then Open Graph scraping, with
  SSRF guards on the server-side fetch.

## Running it

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.

```sh
# backend → http://localhost:8000  (docs at /docs)
cd backend
uv run alembic upgrade head      # create or migrate twitter.db
uv run uvicorn main:app --reload

# frontend → http://localhost:5173
cd frontend
npm install
npm run dev

# optional: fan-out on write needs Redis and a worker
cd backend
uv run python -m app.worker
```

Configuration lives in `backend/.env` (see `Settings` in `app/core/config.py`).
Redis is optional only with `RATE_LIMIT_ENABLED=false` — the limiter fails
closed, so with it on, limited endpoints answer 503 without Redis. Without
`SMTP_HOST`, confirmation and reset mail is printed to the API log instead of
sent (refused in production configs).

After changing a model:

```sh
cd backend
uv run alembic revision --autogenerate -m "what changed"
uv run alembic upgrade head
uv run alembic check                 # passes when models and schema agree
```

## Tests

```sh
cd backend && uv run pytest -q     # API, timeline, link preview
cd frontend && npm run typecheck   # tsc -b
```

## Monitoring

`GET /metrics` is a Prometheus scrape target focused on the paths that fail
silently: RQ queue depth / failed jobs / workers, swallowed Redis failures
(`chirp_redis_failures_total`), per-route traffic and latency labelled by route
template, timeline cache hit/miss, and SSE connections. Details in
[`docs/design-notes.md`](docs/design-notes.md#monitoring).

## Deploying

`git pull && ./deploy/deploy.sh` on the server. See [`deploy/`](deploy/README.md).

## Layout

```
backend/app/
  api/routes/     thin HTTP handlers
  repositories/   SQLAlchemy queries
  services/       timeline strategies, link preview unfurling
  models/         Post, User, Follow, Like, FeedItem, Notification
  schemas/        Pydantic request/response models
backend/alembic/  migrations; env.py reads the URL from app settings
frontend/src/     App.tsx, components.tsx, api.ts, types.ts
docs/             design-notes.md, benchmark results, archive/
```
