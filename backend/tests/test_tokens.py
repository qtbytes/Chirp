"""
The mailed-token store, tested directly.

Routing tests could not pin single-use redemption: `reset_password` also
bulk-revokes, and confirming an address clears `pending_email`, so a redeem that
merely *peeked* instead of consuming still produced the right status codes.
Defense in depth is good and it also hides the defect. These tests hold
`redeem_token` to the guarantee on its own.
"""

import pytest
from app.core import tokens
from app.core.tokens import (
    TokenPurpose,
    issue_token,
    redeem_token,
    revoke_tokens,
)

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
    token = issue_token(RESET, user_id=7, ttl_seconds=0)
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

    assert token not in tokens._memory_tokens
    assert all(token not in str(key) for key in tokens._memory_tokens)
    assert tokens._digest(token) in tokens._memory_tokens


def test_two_tokens_are_never_the_same() -> None:
    minted = {issue_token(RESET, user_id=7, ttl_seconds=60) for _ in range(50)}
    assert len(minted) == 50


@pytest.mark.parametrize("garbage", ["", "not-a-token", "x" * 200])
def test_garbage_redeems_to_nothing(garbage: str) -> None:
    assert redeem_token(RESET, garbage) is None


def test_the_redis_backend_behaves_exactly_like_the_fallback(monkeypatch) -> None:
    """
    The two backends must not diverge, and once did.

    Redis namespaces its keys by purpose, so offering a token to the wrong
    endpoint simply misses. The in-process dict is keyed by digest alone, so it
    had to check the purpose by hand -- and originally consumed the token before
    doing so, letting anyone destroy a confirmation link by POSTing it to
    /auth/reset-password. Run the same script against the real store.
    """
    monkeypatch.undo()  # restore the real get_redis_client that conftest stubbed
    from app.db.redis_client import get_redis_client

    if get_redis_client() is None:
        pytest.skip("redis is not running")

    # Without this the undo() above could quietly fail and leave the module on
    # the in-process dict, turning this into a duplicate of the tests above.
    assert tokens.get_redis_client() is not None, "not exercising the Redis backend"
    assert not tokens._memory_tokens

    user_id = 987_654
    revoke_tokens(RESET, user_id)
    revoke_tokens(VERIFY, user_id)

    verification = issue_token(VERIFY, user_id, ttl_seconds=60)
    reset = issue_token(RESET, user_id, ttl_seconds=60)
    try:
        # wrong purpose: refused, and not consumed
        assert redeem_token(RESET, verification) is None
        assert redeem_token(VERIFY, reset) is None

        # right purpose: works exactly once
        assert redeem_token(VERIFY, verification) == user_id
        assert redeem_token(VERIFY, verification) is None

        assert redeem_token(RESET, reset) == user_id
        assert redeem_token(RESET, reset) is None
    finally:
        revoke_tokens(RESET, user_id)
        revoke_tokens(VERIFY, user_id)
