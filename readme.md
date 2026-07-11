# Chirp

A full-stack Twitter clone built to explore the system-design questions behind a
social timeline — fan-out strategies, cursor pagination, caching, and background
work — with a real UI on top rather than a toy script.

- **Backend** — FastAPI + SQLAlchemy 2.0 + Pydantic v2, SQLite by default, Redis
  and an RQ worker when available.
- **Frontend** — React 19 + TypeScript + Vite, React Router, no CSS framework.

## Features

Posting with images and video, threaded comments and replies, likes, quote
tweets, follows with followers/following lists, profiles with editable bio and
avatar, blocking, email confirmation, password change and reset, active session
management, live notifications over SSE, emoji picker, and Open Graph link
preview cards.

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

**"For you" is ranked, and that is why it can't be precomputed.** `GET
/timeline/for-you` does not order by time. Each candidate is scored `(base +
weighted engagement) × time-decay × affinity`, where decay halves engagement
every `ranking_half_life_hours` and affinity lifts authors the viewer follows or
has liked. So a day-old post with real engagement can sit above a fresh empty
one — something the `created_at|id` sort structurally cannot do, since there the
timestamp always wins and engagement only settles exact ties. The score depends
on *who* is viewing and on *when*, so — unlike fan-out on write, which pushes one
identical chronological row to every follower — it is computed at read time over
a bounded candidate pool (the `ranking_candidate_pool_size` newest posts, which
also caps the work the old unbounded scan did not). That is the crux of the
read-vs-write comparison: a chronological feed precomputes; a ranked one cannot.

Ranked pagination needs a different cursor. It carries `reference_time|score|id`,
and the reference time — "now" as of the first page — is frozen into it so every
page of one scroll decays against the same clock; otherwise page two would score
posts a few seconds staler and the boundary could drift. The scorer itself
(`app/services/ranking.py`) is a pure function of counts, age, and affinity, so
the ranking policy is unit-tested without a database or a clock.

**Redis holds sessions; caching is best-effort.** The timeline and link-preview
caches degrade gracefully when Redis is unreachable — fan-out falls back to
inline execution, caches simply miss. Sessions and rate limiting do not: both
fail closed with a 503 rather than silently dropping their guarantees.

**Live notifications push over SSE, but the event is only a nudge.** The unread
badge used to poll `unread-count` on a timer. Now `GET /notifications/stream` is
a Server-Sent Events stream backed by Redis pub/sub: when something notifies a
user, a message is published to their channel and forwarded to their open tabs.
The event carries no count — the client re-reads the authoritative number on
receiving it — so the badge can never drift from the database, and a browser that
can't hold the stream (or a backend with no Redis, which answers the stream 503)
still recovers on a slow fallback poll. The publish is deferred to a SQLAlchemy
`after_commit` hook keyed off the recipient id staged during `add_notification`,
so a subscriber is woken only for a notification that actually committed — never
one a rollback discarded, and never racing the client's re-read against a
not-yet-committed row. The stream is an async generator polling pub/sub
non-blockingly, so each connection costs an asyncio task rather than a threadpool
worker; a production build would move to `redis.asyncio`.

Notifications also paginate by the same `(created_at, id)` cursor as the feeds,
and `is_read` is settable per row (`POST /notifications/{id}/read`, guarded by
recipient id) as well as in bulk — so opening the page no longer force-marks
everything read; a notification is marked read when it's opened, or all at once
from the header.

**Repeatable actions coalesce, so toggling can't spam.** A like can be turned off
and on all day, and a follow the same; left alone, each re-like would stack
another "liked your tweet" on the owner. `add_notification` dedupes on
`(recipient, actor, type, post_id)` — the tuple that means "the same
notification." For a like, `post_id` is the liked post and stays constant across
re-likes; for a follow it's `NULL` with a fixed actor and recipient; for a
comment, reply, or quote it's the *new* post's own id, unique per action, so
those never collapse. The row is deliberately kept when the like or follow is
undone: it records that the action happened, and keeping it is exactly what stops
the next re-like from minting a fresh one (and a re-notify nudge). The badge and
the live stream are both driven off that create, so neither can be spammed by a
toggle either.

**Sessions are server-side.** The cookie holds an opaque, HMAC-signed session id;
Redis maps it to a user id under a sliding idle TTL. The signature is verified
before the store is consulted, so a forged id never costs a round trip. Logout
deletes the record, which means a captured cookie stops working the moment its
owner logs out — the thing a self-contained token (JWT included) cannot do.

`POST /auth/change-password` is where that pays off: you change a password
because the old one leaked, so every session minted with it is revoked and the
caller's own device is handed a fresh one. Sessions are revoked *before* the new
hash is written — if the store is unreachable the request fails having changed
nothing, where the other order would leave the new password in place and the
leaked sessions alive. Proving knowledge of the current password is required, so
a stolen cookie alone cannot take the account over.

**Sessions are also reviewable.** Because the store already indexes every
session by user, `GET /auth/sessions` can list them with the IP, user agent, and
last-seen time each was stamped with — enough to spot a login you don't
recognise. `POST /auth/logout-others` then revokes all but the current one (the
same blast radius as change-password, without changing the password), and
`DELETE /auth/sessions/{id}` ends a single one. The `id` is `sha256(sid)`, never
the session id itself: the sid is half of a bearer credential, so it is treated
like the mailed tokens — stored and compared, never returned to a browser. A
handle only matches within the caller's own index, so it cannot reach another
account's session, and ending the *current* session is refused here so the one
endpoint that clears the cookie stays `POST /auth/logout`.

**A claimed address is not a confirmed one.** `users.email` is the address a
user has *proven* they control; `users.pending_email` is one they have merely
claimed. Registration and email changes write the claim, and only a redeemed
token promotes it. Just `email` is uniquely indexed and just `email` is matched
by password reset — so two people may both claim an address, whichever confirms
it wins, and the loser's claim never promotes. Keeping the claim out of the
unique index is what stops an address being squatted by someone who cannot
receive its mail.

Changing the address needs the current password, and that requirement is the
whole reason it is not a field on `PATCH /users/me`. Reset mail goes to the
confirmed address; if a stolen cookie could repoint it, the thief would set
their own address, click "forgot password", and own the account — walking
straight around change-password's current-password check. The confirmed address
also does not move until the *new* one is confirmed, so even a thief who knows
the password cannot silently divert reset mail.

**`forgot-password` always answers 202.** For a real account, an unknown
address, and a merely-claimed one alike. Anything else turns the endpoint into
an oracle for "does this person have an account here", which on a social network
is a disclosure in itself. It is also why a mailer failure is logged rather than
raised: the only requests that reach the mailer are the ones where an account
exists, so a 503 there would answer the very question the uniform 202 exists to
hide.

**Mailed tokens are stored hashed, single-use, and revocable.** Redis holds
`sha256(token)`, never the token — a reset link is a bearer credential for the
half hour it lives, and a keyspace dump should yield nothing redeemable.
Redemption is a `GETDEL`, so two requests racing one mailed link cannot both
win. Each purpose keeps a per-user index, so asking for a new link kills the
previous one, and changing or resetting a password kills every link outstanding
— including one an attacker requested.

Resetting revokes every session, for the same reason changing does, and
deliberately does *not* sign the caller in: whoever holds the link may be
whoever read the mailbox. As with change-password, sessions are revoked
**before** the new hash is written.

Accounts predating the email column have neither address. They log in normally
and cannot reset until they add one; `deploy/set_password.py` remains the
operator's way in.

**One rate-limit bucket per name, and the name is the config key.**
`rate_limiter("like")` reads `rate_limit_like_{max_requests,window_seconds}` from
`Settings` *on every request*, and refuses to build a dependency for a bucket that
has no settings. Passing the numbers as arguments instead — as this once did —
froze them at import, which is how three configured limits ended up wired to
nothing. A test asserts the settings and the call sites still name the same
buckets, in both directions.

Writes bucket by user, so a limit follows the account rather than the network it
dials in from. `/auth/login` and `/auth/register` bucket by IP: a caller guessing
passwords may hold a valid session of their own, and keying on it would hand them
a fresh allowance per guess. That throttles one host grinding a credential dump,
not the same dump replayed from a botnet. Login is deliberately *not* throttled
per username — a username bucket lets anyone lock a known account out on demand.

A 429 carries `Retry-After`, computed from the oldest request still inside the
sliding window rather than the window length, so it shrinks as the window slides.

**Alembic owns the schema, and adopting it found real drift.** Before, importing
the app ran `create_all()` plus a helper that bolted missing nullable columns
onto SQLite at startup. That silently hid two things: `create_all()` skips a
table that already exists — *including its indexes* — and `posts` had been
hand-created, so `ix_posts_created_at` and `ix_posts_user_id` never existed in
any deployed database, even though the model declared both. The "for you" feed
sorts every row by `created_at`.

Revision `0001` is the schema the models describe. Databases that predate
Alembic are stamped with it rather than running it (`_adopt_pre_alembic_database`
in `alembic/env.py`), so `alembic upgrade head` is the one safe command
everywhere. Revision `0002` then reconciles what those older databases actually
had: it creates the two missing indexes, drops the vestigial `retweets` table,
and rebuilds `posts`, `likes` and `notifications` — SQLite cannot `ALTER` a
column type or add a foreign key in place, so `batch_alter_table` copies each
into a correctly-defined replacement. `alembic check` now passes against a fresh
database and an upgraded production one alike, which is the property that keeps
the next autogenerated migration honest.

**Link previews unfurl any URL.** One generic path — an oEmbed provider lookup,
then Open Graph / Twitter Card / `<title>` scraping — covers GitHub, YouTube,
Steam and the long tail without per-site adapters. Because the server fetches
user-supplied URLs, it is SSRF-guarded: scheme allowlist, IP-literal and internal
hostname blocking always on, plus an optional resolved-IP check for direct
fetches (see `link_preview_*` in `app/core/config.py`).

**Blocking is one read filter and one write guard, and it is symmetric.** A block
makes two accounts mutually invisible — neither the blocker nor the blocked can
see the other's tweets, replies, or notifications, and neither may follow, like,
comment on, or quote the other. That symmetry is the whole point: if a block only
hid the blocked user's content from the blocker, the blocker would still surface
in the blocked user's timeline and mentions, which is exactly the contact the
block exists to sever. `block_user` deletes any `Follow` in *both* directions and
is idempotent.

The read side is one function: `block_repository.hidden_user_ids(viewer)` returns
`{accounts the viewer blocked} ∪ {accounts that blocked the viewer}`, and every
list path threads it in as an `exclude_*_ids` filter — home and "for you"
timelines, profile tweets and replies, the reply tree, notifications, and user
discovery. The write side is one guard: `blocks_between(a, b)` checks either
direction, and the repository layer raises before a follow, like, comment, or
quote can be written, so a route added later inherits the rule for free. A
blocked interaction is reported as **404, never 403** — a 403 would confirm the
block exists, and "they blocked you" is never surfaced. One known gap: a quoted
post embedded in someone else's tweet is not yet filtered by the viewer's block,
because the embed is serialised without the viewer's hidden set.

## Running it

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.

Redis is optional only with `RATE_LIMIT_ENABLED=false` in `backend/.env`. The
limiter fails closed, so with it on — the default — every limited endpoint,
including the timeline and login, answers 503 without Redis. Sessions, mailed
tokens, and the timeline cache do fall back on their own.

SMTP is optional locally: with no `SMTP_HOST`, confirmation and reset mail is
printed to the API log instead of sent, so you can follow the link without
running a mail server. That console sender is refused in a production
configuration, where a reset token in `journalctl` would be a credential lying
around.

```sh
# backend → http://localhost:8000  (docs at /docs)
cd backend
uv run alembic upgrade head      # create or migrate twitter.db
uv run uvicorn main:app --reload

# frontend → http://localhost:5173
cd frontend
npm install
npm run dev
```

To exercise fan-out on write, start Redis and a worker alongside the API:

```sh
cd backend
uv run python -m app.worker
```

Configuration is read from `backend/.env` (see `Settings` in
`app/core/config.py`). Importing the app no longer touches the schema, so a new
checkout needs `alembic upgrade head` once before the API will answer.

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
docs/archive/     superseded design + benchmark notes
```
