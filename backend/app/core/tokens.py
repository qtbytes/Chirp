"""
Single-use, expiring tokens mailed to a user: email confirmation and password
reset.

Three properties, each load-bearing:

- **Stored hashed.** Redis holds ``sha256(token)``, never the token. A dump of
  the keyspace, or a slow-log line, yields nothing an attacker can redeem. This
  is the same reason a password table stores hashes; a reset token *is* a
  credential for the minute it lives.
- **Single-use.** Redemption is a ``GETDEL``: the read and the delete are one
  atomic step, so two requests racing the same mailed link cannot both win.
- **Revocable in bulk.** Each purpose keeps a per-user index, so changing or
  resetting a password can invalidate every reset link outstanding for that
  account -- including ones an attacker requested.

Lookups are by derived key, so there is no secret to compare and no timing side
channel to protect: an attacker who cannot produce the token cannot produce its
hash, and a wrong hash simply misses.

Fallback: as with sessions, an unreachable Redis falls back to an in-process
dict for local development, and that fallback is refused in a production
configuration (``session_cookie_secure``) so tokens never silently become
per-worker.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from enum import Enum

from app.core.config import settings
from app.db.redis_client import get_redis_client
from redis.exceptions import RedisError


class TokenPurpose(str, Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


class TokenBackendUnavailable(RuntimeError):
    """The token store cannot be reached and no safe fallback is allowed."""


# digest -> (purpose, user_id, expires_at_monotonic)
_memory_tokens: dict[str, tuple[str, int, float]] = {}


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _token_key(purpose: TokenPurpose, digest: str) -> str:
    return f"token:{purpose.value}:{digest}"


def _user_index_key(purpose: TokenPurpose, user_id: int) -> str:
    return f"token_user:{purpose.value}:{user_id}"


def _redis_or_memory():
    try:
        client = get_redis_client()
    except RedisError:
        client = None

    if client is not None:
        return client

    if settings.session_cookie_secure:
        raise TokenBackendUnavailable(
            "Redis is unavailable and in-process tokens are unsafe in production."
        )
    return None


def _memory_purge_expired() -> None:
    now = time.monotonic()
    for digest in [d for d, (_, _, exp) in _memory_tokens.items() if exp <= now]:
        _memory_tokens.pop(digest, None)


def issue_token(purpose: TokenPurpose, user_id: int, ttl_seconds: int) -> str:
    """Mint a token for ``user_id`` and return it. Only the caller ever sees it."""
    raw_token = secrets.token_urlsafe(32)
    digest = _digest(raw_token)
    client = _redis_or_memory()

    if client is None:
        _memory_purge_expired()
        _memory_tokens[digest] = (purpose.value, user_id, time.monotonic() + ttl_seconds)
        return raw_token

    try:
        pipeline = client.pipeline(transaction=True)
        pipeline.setex(_token_key(purpose, digest), ttl_seconds, str(user_id))
        # The index outlives any single token, so it carries the same TTL and is
        # pushed forward whenever another token is issued for this user.
        pipeline.sadd(_user_index_key(purpose, user_id), digest)
        pipeline.expire(_user_index_key(purpose, user_id), ttl_seconds)
        pipeline.execute()
    except RedisError as exc:
        raise TokenBackendUnavailable("failed to store token") from exc

    return raw_token


def redeem_token(purpose: TokenPurpose, raw_token: str) -> int | None:
    """
    Consume a token and return its user id, or None if it is unknown or expired.

    Redeeming is destructive whether or not the caller goes on to succeed. A
    reset link that is clicked twice must not work twice.
    """
    if not raw_token:
        return None

    digest = _digest(raw_token)
    client = _redis_or_memory()

    if client is None:
        _memory_purge_expired()
        entry = _memory_tokens.get(digest)
        if entry is None:
            return None
        stored_purpose, user_id, _ = entry

        # A token minted to confirm an address must not double as a reset link --
        # and offering it to the wrong endpoint must not *consume* it either, or
        # anyone holding a confirmation link could destroy it by POSTing it to
        # /auth/reset-password. Check the purpose before spending the token.
        #
        # Redis gets this for free: its keys are namespaced by purpose, so the
        # GETDEL below simply misses. This branch has to do it by hand, and the
        # two must not disagree.
        if stored_purpose != purpose.value:
            return None

        del _memory_tokens[digest]
        return user_id

    try:
        raw = client.getdel(_token_key(purpose, digest))
        if raw is None:
            return None
        user_id = int(raw)
        client.srem(_user_index_key(purpose, user_id), digest)
        return user_id
    except RedisError as exc:
        raise TokenBackendUnavailable("failed to read token") from exc
    except (TypeError, ValueError):
        return None


def revoke_tokens(purpose: TokenPurpose, user_id: int) -> int:
    """Invalidate every outstanding token of one purpose for a user."""
    client = _redis_or_memory()

    if client is None:
        _memory_purge_expired()
        victims = [
            digest
            for digest, (stored_purpose, uid, _) in _memory_tokens.items()
            if uid == user_id and stored_purpose == purpose.value
        ]
        for digest in victims:
            _memory_tokens.pop(digest, None)
        return len(victims)

    try:
        digests = client.smembers(_user_index_key(purpose, user_id))
        if not digests:
            return 0
        keys = [
            _token_key(purpose, d.decode() if isinstance(d, bytes) else d)
            for d in digests
        ]
        client.delete(*keys)
        client.delete(_user_index_key(purpose, user_id))
        return len(keys)
    except RedisError as exc:
        raise TokenBackendUnavailable("failed to revoke tokens") from exc
