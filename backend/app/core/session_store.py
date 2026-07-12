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

Each session record also carries a little metadata -- when it was created, when
it was last seen, and the IP and user-agent it was created from -- so a user can
review their active sessions and end the ones they do not recognise. That
metadata is descriptive only; authentication never trusts it.

The cookie is still HMAC-signed (``<sid>.<signature>``). The signature is
checked before the store is consulted, so a forged or tampered id is rejected
without spending a Redis round trip.

The listing exposes ``sha256(sid)`` as an opaque handle, never the sid itself:
the sid is half of a bearer credential, so it is treated like the mailed tokens
in ``tokens.py`` -- stored and compared, never handed back to a browser.

Redis is required. When it cannot be reached the caller gets
``SessionBackendUnavailable`` (surfaced as 503), so auth fails closed rather than
silently degrading.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from app.core.config import settings
from app.core.security import sign_payload
from app.db.redis_client import get_redis_client
from redis import Redis
from redis.exceptions import RedisError, ResponseError

_SESSION_PREFIX = "session:"
_USER_INDEX_PREFIX = "session_user:"


@dataclass
class SessionInfo:
    """One active session, as shown to its owner. ``id`` is ``sha256(sid)``."""

    id: str
    created_at: float  # unix epoch seconds
    last_seen: float  # unix epoch seconds
    ip: str | None
    user_agent: str | None


class SessionBackendUnavailable(RuntimeError):
    """The session store (Redis) cannot be reached."""


def _ttl() -> int:
    return settings.session_ttl_seconds


def _session_key(sid: str) -> str:
    return f"{_SESSION_PREFIX}{sid}"


def _user_index_key(user_id: int) -> str:
    return f"{_USER_INDEX_PREFIX}{user_id}"


def _dec(value) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _is_wrong_type(exc: RedisError) -> bool:
    """
    True for Redis's WRONGTYPE error.

    A session used to be a plain string ``session:<sid> -> user_id``; it is now a
    hash. A hash command against a leftover string key raises WRONGTYPE. Rather
    than 503 every session minted before this change, such a key is treated as an
    unreadable legacy session: the holder is asked to log in again (which mints a
    hash), and the stale key is pruned. A real outage is a different RedisError
    and still fails closed.
    """
    return isinstance(exc, ResponseError) and "WRONGTYPE" in str(exc)


def session_handle(sid: str) -> str:
    """The opaque, non-reversible id a session is known by to its owner."""
    return hashlib.sha256(sid.encode()).hexdigest()


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


def session_id_from_cookie(raw_cookie: str | None) -> str | None:
    """Public wrapper: the verified sid behind a cookie, or None."""
    return _split_cookie(raw_cookie)


def _client() -> Redis:
    """The Redis client, or ``SessionBackendUnavailable`` if it cannot connect."""
    try:
        return get_redis_client()
    except RedisError as exc:
        raise SessionBackendUnavailable("session store is unavailable") from exc


def create_session(
    user_id: int,
    ttl_seconds: int | None = None,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a session and return the signed cookie value."""
    ttl = _ttl() if ttl_seconds is None else ttl_seconds
    sid = secrets.token_urlsafe(32)
    client = _client()
    now = time.time()

    try:
        pipeline = client.pipeline(transaction=True)
        pipeline.hset(
            _session_key(sid),
            mapping={
                "uid": str(user_id),
                "created": repr(now),
                "seen": repr(now),
                "ip": ip or "",
                "ua": user_agent or "",
            },
        )
        pipeline.expire(_session_key(sid), ttl)
        # Index so every session for a user can be revoked at once. The index
        # outlives individual sessions, so give it the same TTL and refresh it
        # whenever a session is created.
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

    ``refresh`` slides the TTL forward and updates the last-seen timestamp, so an
    active user is not logged out mid-session. Pass False for read-only lookups
    (e.g. the rate limiter) that should not extend a session as a side effect.
    """
    sid = _split_cookie(raw_cookie)
    if sid is None:
        return None

    client = _client()

    try:
        raw = client.hget(_session_key(sid), "uid")
        if raw is None:
            return None
        user_id = int(raw)
        if refresh:
            pipeline = client.pipeline(transaction=True)
            pipeline.hset(_session_key(sid), "seen", repr(time.time()))
            pipeline.expire(_session_key(sid), _ttl())
            pipeline.expire(_user_index_key(user_id), _ttl())
            pipeline.execute()
        return user_id
    except RedisError as exc:
        if _is_wrong_type(exc):
            # A pre-hash session cookie. Invalid under the new format -> re-login.
            return None
        raise SessionBackendUnavailable("failed to read session") from exc
    except (TypeError, ValueError):
        return None


def destroy_session(raw_cookie: str | None) -> None:
    """Invalidate one session server-side. Safe to call with a bad cookie."""
    sid = _split_cookie(raw_cookie)
    if sid is None:
        return

    client = _client()

    try:
        try:
            raw = client.hget(_session_key(sid), "uid")
        except ResponseError:
            # Legacy string session: we cannot read its uid, but deleting the key
            # is what matters. Its index entry is pruned by the next listing.
            raw = None
        client.delete(_session_key(sid))
        if raw is not None:
            client.srem(_user_index_key(int(raw)), sid)
    except RedisError as exc:
        raise SessionBackendUnavailable("failed to delete session") from exc
    except (TypeError, ValueError):
        return


def list_user_sessions(user_id: int) -> list[SessionInfo]:
    """Every live session for a user, most recently seen first."""
    client = _client()

    try:
        sids = [_dec(s) for s in client.smembers(_user_index_key(user_id))]
        infos: list[SessionInfo] = []
        stale: list[str] = []
        for sid in sids:
            try:
                data = client.hgetall(_session_key(sid))
            except ResponseError as exc:
                if not _is_wrong_type(exc):
                    raise
                # A legacy string session lingering in the index. Unreadable
                # here; drop it rather than fail the whole listing.
                stale.append(sid)
                continue
            if not data:
                # The session expired but its id lingers in the index; drop it.
                stale.append(sid)
                continue
            fields = {_dec(k): _dec(v) for k, v in data.items()}
            infos.append(
                SessionInfo(
                    id=session_handle(sid),
                    created_at=float(fields.get("created") or 0.0),
                    last_seen=float(fields.get("seen") or 0.0),
                    ip=fields.get("ip") or None,
                    user_agent=fields.get("ua") or None,
                )
            )
        if stale:
            client.srem(_user_index_key(user_id), *stale)
        infos.sort(key=lambda info: info.last_seen, reverse=True)
        return infos
    except RedisError as exc:
        raise SessionBackendUnavailable("failed to list sessions") from exc


def revoke_user_sessions(user_id: int, *, keep_sid: str | None = None) -> int:
    """
    Invalidate every session for a user, optionally sparing ``keep_sid``.

    Returns how many were removed. ``keep_sid`` is how "log out everywhere else"
    keeps the caller's own device signed in, exactly as change-password does.
    """
    client = _client()

    try:
        sids = [_dec(s) for s in client.smembers(_user_index_key(user_id))]
        victims = [s for s in sids if s != keep_sid]
        if not victims:
            return 0
        client.delete(*[_session_key(s) for s in victims])
        if keep_sid is None:
            client.delete(_user_index_key(user_id))
        else:
            client.srem(_user_index_key(user_id), *victims)
        return len(victims)
    except RedisError as exc:
        raise SessionBackendUnavailable("failed to revoke sessions") from exc


def revoke_session_by_handle(user_id: int, handle: str) -> bool:
    """
    End one of a user's sessions by its opaque ``sha256(sid)`` handle.

    Only the caller's own sessions are searched, so a handle cannot be used to
    reach another account's session. Returns True if one was found and removed.
    """
    client = _client()

    try:
        sids = [_dec(s) for s in client.smembers(_user_index_key(user_id))]
        for sid in sids:
            if session_handle(sid) == handle:
                client.delete(_session_key(sid))
                client.srem(_user_index_key(user_id), sid)
                return True
        return False
    except RedisError as exc:
        raise SessionBackendUnavailable("failed to revoke session") from exc
