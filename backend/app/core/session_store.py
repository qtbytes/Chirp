"""
Server-side sessions.

The cookie carries an opaque random id, not the user id. The mapping
``session id -> user id`` lives in Redis with a TTL, which buys two things the
previous self-contained cookie could not offer:

- **Expiry.** The old cookie was ``base64(user_id) + HMAC`` with no timestamp,
  so a value that leaked once was valid forever.
- **Revocation.** Logging out now deletes the server-side record, so a captured
  cookie stops working immediately. ``revoke_user_sessions`` logs a user out
  everywhere without rotating the global signing key.

The cookie is still HMAC-signed (``<sid>.<signature>``). The signature is
checked before the store is consulted, so a forged or tampered id is rejected
without spending a Redis round trip.

Fallback: when Redis is unavailable the store falls back to an in-process dict,
which is fine for local development and tests but obviously does not survive a
restart or span workers. In a production configuration (``session_cookie_secure``)
that fallback is refused and the caller gets ``SessionBackendUnavailable``, so
auth fails closed rather than silently degrading to per-worker sessions.
"""

from __future__ import annotations

import hmac
import secrets
import time

from app.core.config import settings
from app.core.security import sign_payload
from app.db.redis_client import get_redis_client
from redis.exceptions import RedisError

_SESSION_PREFIX = "session:"
_USER_INDEX_PREFIX = "session_user:"

# sid -> (user_id, expires_at_monotonic)
_memory_sessions: dict[str, tuple[int, float]] = {}


class SessionBackendUnavailable(RuntimeError):
    """The session store cannot be reached and no safe fallback is allowed."""


def _ttl() -> int:
    return settings.session_ttl_seconds


def _session_key(sid: str) -> str:
    return f"{_SESSION_PREFIX}{sid}"


def _user_index_key(user_id: int) -> str:
    return f"{_USER_INDEX_PREFIX}{user_id}"


def _split_cookie(raw_cookie: str | None) -> str | None:
    """Return the session id if the cookie is well-formed and correctly signed."""
    if not raw_cookie:
        return None

    try:
        sid, signature = raw_cookie.split(".", 1)
    except ValueError:
        return None

    if not sid or not hmac.compare_digest(signature, sign_payload(sid)):
        return None

    return sid


def _redis_or_memory():
    """
    Return the Redis client, or None to use the in-process fallback.

    Refuses the fallback in a production configuration: per-worker, non-durable
    sessions are worse than a hard failure.
    """
    try:
        client = get_redis_client()
    except RedisError:
        client = None

    if client is not None:
        return client

    if settings.session_cookie_secure:
        raise SessionBackendUnavailable(
            "Redis is unavailable and in-process sessions are unsafe in production."
        )
    return None


def _memory_purge_expired() -> None:
    now = time.monotonic()
    for sid in [s for s, (_, exp) in _memory_sessions.items() if exp <= now]:
        _memory_sessions.pop(sid, None)


def create_session(user_id: int, ttl_seconds: int | None = None) -> str:
    """Create a session and return the signed cookie value."""
    ttl = _ttl() if ttl_seconds is None else ttl_seconds
    sid = secrets.token_urlsafe(32)
    client = _redis_or_memory()

    if client is None:
        _memory_purge_expired()
        _memory_sessions[sid] = (user_id, time.monotonic() + ttl)
    else:
        try:
            pipeline = client.pipeline(transaction=True)
            pipeline.setex(_session_key(sid), ttl, str(user_id))
            # Index so every session for a user can be revoked at once. The
            # index outlives individual sessions, so give it the same TTL and
            # refresh it whenever a session is created.
            pipeline.sadd(_user_index_key(user_id), sid)
            pipeline.expire(_user_index_key(user_id), ttl)
            pipeline.execute()
        except RedisError as exc:
            raise SessionBackendUnavailable("failed to persist session") from exc

    return f"{sid}.{sign_payload(sid)}"


def resolve_session(raw_cookie: str | None, refresh: bool = True) -> int | None:
    """
    Return the user id behind a cookie, or None if it is absent, forged, or
    expired.

    ``refresh`` slides the TTL forward, so an active user is not logged out
    mid-session. Pass False for read-only lookups (e.g. the rate limiter) that
    should not extend a session as a side effect.
    """
    sid = _split_cookie(raw_cookie)
    if sid is None:
        return None

    client = _redis_or_memory()

    if client is None:
        _memory_purge_expired()
        entry = _memory_sessions.get(sid)
        if entry is None:
            return None
        user_id, _ = entry
        if refresh:
            _memory_sessions[sid] = (user_id, time.monotonic() + _ttl())
        return user_id

    try:
        raw = client.get(_session_key(sid))
        if raw is None:
            return None
        user_id = int(raw)
        if refresh:
            client.expire(_session_key(sid), _ttl())
            client.expire(_user_index_key(user_id), _ttl())
        return user_id
    except RedisError as exc:
        raise SessionBackendUnavailable("failed to read session") from exc
    except (TypeError, ValueError):
        return None


def destroy_session(raw_cookie: str | None) -> None:
    """Invalidate one session server-side. Safe to call with a bad cookie."""
    sid = _split_cookie(raw_cookie)
    if sid is None:
        return

    client = _redis_or_memory()

    if client is None:
        _memory_sessions.pop(sid, None)
        return

    try:
        raw = client.get(_session_key(sid))
        client.delete(_session_key(sid))
        if raw is not None:
            client.srem(_user_index_key(int(raw)), sid)
    except RedisError as exc:
        raise SessionBackendUnavailable("failed to delete session") from exc
    except (TypeError, ValueError):
        return


def revoke_user_sessions(user_id: int) -> int:
    """Invalidate every session for a user. Returns how many were removed."""
    client = _redis_or_memory()

    if client is None:
        victims = [s for s, (uid, _) in _memory_sessions.items() if uid == user_id]
        for sid in victims:
            _memory_sessions.pop(sid, None)
        return len(victims)

    try:
        sids = client.smembers(_user_index_key(user_id))
        if not sids:
            return 0
        keys = [_session_key(s.decode() if isinstance(s, bytes) else s) for s in sids]
        client.delete(*keys)
        client.delete(_user_index_key(user_id))
        return len(keys)
    except RedisError as exc:
        raise SessionBackendUnavailable("failed to revoke sessions") from exc
