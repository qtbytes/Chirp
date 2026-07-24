"""
How many characters a given user may put in a post.

Everyone starts at 280. Two things raise it, and both are earned rather than
bought:

* confirming an email address adds a flat 1000 -- the one bonus a new account
  can grant itself the day it registers;
* every whole month since registration adds 100, counted over the first year
  only (a launch promotion, hence the 12-month ceiling).

The two are summed and then clipped to MAX_BONUS, so no combination of them can
take a post past GLOBAL_MAX_POST_LENGTH. That ceiling is what the request
schemas cap at: the per-user limit is enforced in the route, because Pydantic
can only express a constant.

Worked examples, for a user who confirms their email on day one:
    day 0      280 + 1000            = 1280
    1 month    280 + 1000 + 100      = 1380
    2 months   280 + min(1200, 1200) = 1480  (the ceiling, from here on)
and for one who never confirms:
    12 months  280 + 1200            = 1480  (the same ceiling, the slow way)
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User

# What every account can post, confirmed email or not.
BASE_POST_LENGTH = 280

# Added once the address in ``users.email`` exists -- i.e. a mailed token was
# redeemed. A merely claimed ``pending_email`` earns nothing.
VERIFIED_EMAIL_BONUS = 1000

# Added per whole month since registration, for the first year only.
TENURE_BONUS_PER_MONTH = 100
TENURE_BONUS_MAX_MONTHS = 12

# The most the two bonuses can add between them.
MAX_BONUS = 1200

# The hard ceiling every write path validates against.
GLOBAL_MAX_POST_LENGTH = BASE_POST_LENGTH + MAX_BONUS


@dataclass(frozen=True)
class PostLengthAllowance:
    """One user's limit, broken down so the UI can explain how to raise it."""

    limit: int
    base: int
    verified_bonus: int
    tenure_bonus: int
    # Whole months since registration, already clipped to TENURE_BONUS_MAX_MONTHS
    # -- so `tenure_months == TENURE_BONUS_MAX_MONTHS` means the promo is spent.
    tenure_months: int
    email_verified: bool
    # What the limit would be if this user confirmed an email address today --
    # equal to ``limit`` when they already have. The composer quotes it when
    # offering the upgrade, so the size of the prize stays a server-side fact
    # rather than a number the UI has to reconstruct.
    limit_if_email_verified: int = 0
    global_max: int = GLOBAL_MAX_POST_LENGTH

    @property
    def at_global_max(self) -> bool:
        """True when nothing the user does can raise this limit any further."""
        return self.limit >= self.global_max


def whole_months_between(start: datetime, end: datetime) -> int:
    """
    Whole calendar months from ``start`` to ``end``, never negative.

    Calendar months rather than 30-day blocks, so the bonus lands on the day of
    the month the account was created -- someone who joined on the 31st rolls
    over on the last day of a shorter month, which is the usual reading of
    "a month later".
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        # The anniversary day has not arrived yet this month.
        months -= 1
    return max(0, months)


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as the UTC they were stored as."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def post_length_allowance(user: User, now: datetime | None = None) -> PostLengthAllowance:
    """The character budget ``user`` may spend on one post, with its breakdown."""
    now = now or datetime.now(timezone.utc)
    email_verified = user.email is not None

    verified_bonus = VERIFIED_EMAIL_BONUS if email_verified else 0
    months = min(
        whole_months_between(_as_utc(user.created_at), _as_utc(now)),
        TENURE_BONUS_MAX_MONTHS,
    )
    tenure_bonus = months * TENURE_BONUS_PER_MONTH

    # Clip the *sum*, not each part, so the breakdown the UI shows still adds up
    # to the limit it is shown beside.
    bonus = min(MAX_BONUS, verified_bonus + tenure_bonus)
    if verified_bonus > bonus:
        verified_bonus, tenure_bonus = bonus, 0
    else:
        tenure_bonus = bonus - verified_bonus

    # Same sum with the email bonus switched on, so the UI can quote the prize.
    with_email = min(MAX_BONUS, VERIFIED_EMAIL_BONUS + months * TENURE_BONUS_PER_MONTH)

    return PostLengthAllowance(
        limit=BASE_POST_LENGTH + bonus,
        base=BASE_POST_LENGTH,
        verified_bonus=verified_bonus,
        tenure_bonus=tenure_bonus,
        tenure_months=months,
        email_verified=email_verified,
        limit_if_email_verified=BASE_POST_LENGTH + with_email,
    )


def enforce_post_length(db: Session, user_id: int, content: str) -> None:
    """
    Reject ``content`` when it is longer than ``user_id`` is allowed to write.

    Every write path -- new tweet, new comment, reply, and the edits of each --
    goes through here, so raising a limit can never be worked around by editing
    a short post into a long one. The composer already knows the limit and
    blocks submission, so reaching this is either a stale client or a direct
    API call; 422 matches what the schema's own length check would have raised.
    """
    user = db.get(User, user_id)
    if user is None:
        # Callers that care about a missing user check for it themselves; here it
        # simply means there is no allowance to check against.
        return
    allowance = post_length_allowance(user)
    if len(content) > allowance.limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"content is {len(content)} characters; your limit is "
                f"{allowance.limit}"
            ),
        )
