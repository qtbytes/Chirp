# Chirp

A full-stack Twitter clone built to explore the system-design questions behind a
social timeline — fan-out strategies, cursor pagination, caching, and background
work — with a real UI on top rather than a toy script.

- **Backend** — FastAPI + SQLAlchemy 2.0 + Pydantic v2, SQLite by default, Redis
  and an RQ worker when available.
- **Frontend** — React 19 + TypeScript + Vite, React Router, no CSS framework.

## Features

Posting with images and video, threaded comments and replies, likes, quote
tweets, follows, profiles with editable bio and avatar, notifications, emoji
picker, and Open Graph link preview cards.

## Design notes

**One `Post` model for everything.** A tweet, a comment, and a reply are the same
row. `reply_to_id` is `NULL` for a top-level tweet; `root_id` points at the
thread's origin so "all replies under tweet X" stays a single indexed lookup.

**Quote tweets, not a join table.** A quote is an ordinary top-level post with
`quoted_post_id` set, exactly like Twitter. A plain retweet is just a quote with
empty content, and `retweet_count` is derived by counting posts that quote a
given post. There is no `retweets` table.

**Two timeline strategies behind one API.** `GET /timeline/home?strategy=` serves
either:

- `read` — *fan-out on read*: query posts from followed users at request time.
  Light writes, heavy reads.
- `write` — *fan-out on write*: a background RQ job pushes each new post into
  every follower's `feed_items` row. Heavy writes, very fast reads — and the
  celebrity problem shows up here.

Both paginate by cursor (`created_at|id`) rather than offset, so new posts can't
cause skipped or duplicated rows. Repositories fetch `limit + 1` rows to detect
the next page without a second query, and engagement counts are aggregated in
subqueries to avoid N+1.

**Redis is optional.** It caches the first timeline page (short TTL), backs the
rate limiter and the link-preview cache, and runs the fan-out queue. When it is
unreachable every one of those degrades gracefully — fan-out falls back to inline
execution, caches simply miss.

**Link previews unfurl any URL.** One generic path — an oEmbed provider lookup,
then Open Graph / Twitter Card / `<title>` scraping — covers GitHub, YouTube,
Steam and the long tail without per-site adapters. Because the server fetches
user-supplied URLs, it is SSRF-guarded: scheme allowlist, IP-literal and internal
hostname blocking always on, plus an optional resolved-IP check for direct
fetches (see `link_preview_*` in `app/core/config.py`).

## Running it

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node. Redis is
optional.

```sh
# backend → http://localhost:8000  (docs at /docs)
cd backend
uv run uvicorn main:app --reload

# frontend → http://localhost:5173
cd frontend
npm install
npm run dev
```

To exercise fan-out on write, start Redis and a worker alongside the API:

```sh
cd backend
uv run run-rq-worker
```

Configuration is read from `backend/.env` (see `Settings` in
`app/core/config.py`). On startup the dev SQLite schema auto-syncs new nullable
columns, so a restart usually replaces a migration.

## Tests

```sh
cd backend && uv run pytest -q     # API, timeline, link preview
cd frontend && npm run typecheck   # tsc -b
```

## Layout

```
backend/app/
  api/routes/     thin HTTP handlers
  repositories/   SQLAlchemy queries
  services/       timeline strategies, link preview unfurling
  models/         Post, User, Follow, Like, FeedItem, Notification
  schemas/        Pydantic request/response models
frontend/src/     App.tsx, components.tsx, api.ts, types.ts
docs/archive/     superseded design + benchmark notes
```
