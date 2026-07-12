"""
Live updates over Server-Sent Events, backed by Redis pub/sub.

The notification badge used to poll ``GET /notifications/unread-count`` on a
timer. This adds a push path: when something notifies a user, we publish a nudge
to their Redis channel, and an SSE stream (`GET /notifications/stream`) forwards
it to whichever browser tabs that user has open. The client then re-reads the
authoritative count -- the event is a *wake-up*, not the data, so the badge can
never drift from the database.

Two deliberate choices:

- **Publish after commit, not when the row is staged.** ``add_notification``
  only queues the recipient id on the session; the actual publish happens in an
  ``after_commit`` hook. So a subscriber is woken only for a notification that
  truly landed, and never for one a rollback discarded. Publishing before the
  commit would also race the client's re-read against the not-yet-committed row.
- **Best effort.** A failed publish (or no Redis at all) is swallowed: the client
  still polls on a slower timer, so a missed nudge self-heals rather than erroring
  the action that triggered it.

Concurrency: the stream is an async generator that polls pub/sub non-blockingly
and ``await``s between reads, so each open connection is one asyncio task rather
than one thread -- it will not starve the threadpool the sync endpoints share. A
production build would reach for ``redis.asyncio``; this stays on the project's
sync client, which is enough for the demo.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.redis_client import get_redis_client

_CHANNEL_PREFIX = "events:user:"
# Where per-session pending recipient ids are stashed until the commit lands.
_PENDING_KEY = "pending_event_user_ids"
# Comment frames keep proxies from reaping an idle stream and let the browser
# notice a dead connection.
_HEARTBEAT_SECONDS = 15.0
# How often the async loop wakes to check pub/sub. Bounds delivery latency; the
# badge does not need sub-second freshness.
_POLL_INTERVAL_SECONDS = 0.5


def _channel(user_id: int) -> str:
    return f"{_CHANNEL_PREFIX}{user_id}"


def queue_user_event(db: Session, user_id: int) -> None:
    """Mark ``user_id`` to be nudged once the current transaction commits."""
    db.info.setdefault(_PENDING_KEY, set()).add(user_id)


def _publish(user_id: int, payload: dict) -> None:
    # Best-effort on purpose: this runs in an after-commit hook, and the client
    # also polls, so a dropped nudge is recovered -- it must not fail the write
    # that already committed.
    try:
        get_redis_client().publish(_channel(user_id), json.dumps(payload))
    except RedisError:
        pass


@event.listens_for(Session, "after_commit")
def _flush_pending_events(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    for user_id in pending:
        _publish(user_id, {"type": "notification"})


@event.listens_for(Session, "after_rollback")
def _drop_pending_events(session: Session) -> None:
    # The notifications never landed; do not nudge anyone about them.
    session.info.pop(_PENDING_KEY, None)


def format_sse(data: str, event_name: str | None = None) -> str:
    """Render one SSE frame. A bare comment (``: ...``) is a heartbeat."""
    prefix = f"event: {event_name}\n" if event_name else ""
    return f"{prefix}data: {data}\n\n"


async def stream_user_events(user_id: int) -> AsyncIterator[str]:
    """
    Yield SSE frames for one user's channel until the client disconnects.

    Polls pub/sub without blocking and sleeps between reads, so the connection
    costs an asyncio task rather than a worker thread.
    """
    pubsub = get_redis_client().pubsub()
    pubsub.subscribe(_channel(user_id))
    try:
        # An initial comment flushes headers so the browser marks the stream open.
        yield ": connected\n\n"
        last_heartbeat = time.monotonic()
        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=0)
            if message is not None:
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                yield format_sse(data, event_name="notification")
                continue

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                yield ": ping\n\n"
                last_heartbeat = now

            # A cancellation point: on client disconnect Starlette cancels the
            # task here and the finally block tears the subscription down.
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    finally:
        try:
            pubsub.close()
        except RedisError:
            pass
