"""
The mailed-token store, tested directly.

Routing tests could not pin single-use redemption: `reset_password` also
bulk-revokes, and confirming an address clears `pending_email`, so a redeem that
merely *peeked* instead of consuming still produced the right status codes.
Defense in depth is good and it also hides the defect. These tests hold
`redeem_token` to the guarantee on its own.
"""

import time

import pytest
from app.core import tokens
from app.core.tokens import (
    TokenPurpose,
    issue_token,
    redeem_token,
    revoke_tokens,
)
from app.db.redis_client import get_redis_client

RESET = TokenPurpose.PASSWORD_RESET
VERIFY = TokenPurpose.EMAIL_VERIFICATION


def test_a_token_redeems_to_the_user_it_was_minted_for() -> None:
    token = issue_token(RESET, user_id=7, ttl_seconds=60)
    assert redeem_token(RESET, token) == 7


def test_redeeming_consumes_the_token() -> None:
    """
    The property that stops a replayed link.

    Nothing else in this codebase enforces it: an attacker racing two requests
    against one mailed link must lose exactly one of them here.
    """
    token = issue_token(RESET, user_id=7, ttl_seconds=60)

    assert redeem_token(RESET, token) == 7
    assert redeem_token(RESET, token) is None, "the token survived redemption"


def test_a_token_is_bound_to_its_purpose() -> None:
    """A link that confirms an address must not also reset a password."""
    verification = issue_token(VERIFY, user_id=7, ttl_seconds=60)
    reset = issue_token(RESET, user_id=7, ttl_seconds=60)

    assert redeem_token(RESET, verification) is None
    assert redeem_token(VERIFY, reset) is None

    # Rejecting the wrong purpose must not have consumed them either.
    assert redeem_token(VERIFY, verification) == 7
    assert redeem_token(RESET, reset) == 7


def test_an_expired_token_is_gone() -> None:
    # Real Redis rejects SETEX 0, so mint a 1s token and let it lapse.
    token = issue_token(RESET, user_id=7, ttl_seconds=1)
    time.sleep(1.1)
    assert redeem_token(RESET, token) is None


def test_revoking_kills_every_outstanding_token_for_that_user() -> None:
    first = issue_token(RESET, user_id=7, ttl_seconds=60)
    second = issue_token(RESET, user_id=7, ttl_seconds=60)
    other_user = issue_token(RESET, user_id=8, ttl_seconds=60)
    other_purpose = issue_token(VERIFY, user_id=7, ttl_seconds=60)

    assert revoke_tokens(RESET, user_id=7) == 2

    assert redeem_token(RESET, first) is None
    assert redeem_token(RESET, second) is None
    assert redeem_token(RESET, other_user) == 8, "revoked the wrong user"
    assert redeem_token(VERIFY, other_purpose) == 7, "revoked the wrong purpose"


def test_revoking_a_user_with_no_tokens_is_harmless() -> None:
    assert revoke_tokens(RESET, user_id=999) == 0


def test_the_store_never_holds_a_redeemable_token() -> None:
    """A dump of the keyspace, or a stray log line, must yield nothing usable."""
    token = issue_token(RESET, user_id=7, ttl_seconds=60)

    client = get_redis_client()
    keys = [k.decode() if isinstance(k, bytes) else k for k in client.keys("token:*")]
    # The raw token appears nowhere; only a key derived from its hash exists.
    assert keys, "no token was stored"
    assert all(token not in key for key in keys)
    assert client.exists(tokens._token_key(RESET, tokens._digest(token)))


def test_two_tokens_are_never_the_same() -> None:
    minted = {issue_token(RESET, user_id=7, ttl_seconds=60) for _ in range(50)}
    assert len(minted) == 50


@pytest.mark.parametrize("garbage", ["", "not-a-token", "x" * 200])
def test_garbage_redeems_to_nothing(garbage: str) -> None:
    assert redeem_token(RESET, garbage) is None
