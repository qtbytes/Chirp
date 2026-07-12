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

Redis is required. When it cannot be reached the caller gets
``TokenBackendUnavailable`` (surfaced as 503).
"""

from __future__ import annotations

import hashlib
import secrets
from enum import Enum

from app.db.redis_client import get_redis_client
from redis import Redis
from redis.exceptions import RedisError


class TokenPurpose(str, Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


class TokenBackendUnavailable(RuntimeError):
    """The token store (Redis) cannot be reached."""


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _token_key(purpose: TokenPurpose, digest: str) -> str:
    return f"token:{purpose.value}:{digest}"


def _user_index_key(purpose: TokenPurpose, user_id: int) -> str:
    return f"token_user:{purpose.value}:{user_id}"


def _client() -> Redis:
    """The Redis client, or ``TokenBackendUnavailable`` if it cannot connect."""
    try:
        return get_redis_client()
    except RedisError as exc:
        raise TokenBackendUnavailable("token store is unavailable") from exc


def issue_token(purpose: TokenPurpose, user_id: int, ttl_seconds: int) -> str:
    """Mint a token for ``user_id`` and return it. Only the caller ever sees it."""
    raw_token = secrets.token_urlsafe(32)
    digest = _digest(raw_token)
    client = _client()

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
    client = _client()

    try:
        # Keys are namespaced by purpose, so offering a token to the wrong
        # endpoint simply misses the GETDEL -- a confirmation link POSTed to
        # /auth/reset-password neither redeems nor gets consumed.
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
    client = _client()

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
