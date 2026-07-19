import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

# One definition, so registration, login, password change, and
# deploy/set_password.py cannot drift apart on what counts as a valid password.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

# RFC 5321's maximum, and the width of users.email.
EMAIL_MAX_LENGTH = 254


def _normalize_email(value: str) -> str:
    """
    Lowercase and trim, so an address has exactly one spelling in the database.

    RFC 5321 says the local part is case-sensitive; no mail provider in practice
    treats it that way, and honouring the letter of the spec here would let
    Alice@x and alice@x be two accounts, then send a reset for one to the other.
    """
    return value.strip().lower()


Email = Annotated[
    EmailStr,
    Field(max_length=EMAIL_MAX_LENGTH),
    AfterValidator(_normalize_email),
]

# Usernames double as top-level profile URLs (e.g. /alice), so they must not
# collide with existing or likely-future app routes.
RESERVED_USERNAMES = frozenset(
    {
        "search",
        "following",
        "notifications",
        "tweet",
        "home",
        "explore",
        "settings",
        "profile",
        "login",
        "logout",
        "register",
        "about",
        "me",
        # The pages a mailed link lands on. React Router prefers a static segment
        # over /:username, so registering these would not hijack the page -- it
        # would strand the user's own profile behind an unreachable URL.
        "reset-password",
        "verify-email",
        "forgot-password",
        "messages",
        "moderation",
    }
)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    # Required for new accounts. The column stays nullable because the accounts
    # that predate it have none and must keep working.
    email: Email
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("username")
    @classmethod
    def _reject_reserved_username(cls, value: str) -> str:
        lowered = value.lower()
        if lowered in RESERVED_USERNAMES:
            raise ValueError("username is reserved")
        # deleted_<n> is the shape a deleted account's username is rewritten to,
        # so it must never be claimable: otherwise a live account could squat the
        # name a future deletion needs (blocking that deletion), or impersonate a
        # tombstone. This makes deleted_<id> collision-free by construction.
        if re.fullmatch(r"deleted_\d+", lowered):
            raise ValueError("username is reserved")
        return value


class UserSummary(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    created_at: datetime
    avatar_url: str | None = None
    # True once the account is deleted, so the UI can tombstone the author of a
    # post that outlived its owner. Read from the User.is_deleted property.
    is_deleted: bool = False

    model_config = ConfigDict(from_attributes=True)


class CurrentUserOut(UserSummary):
    """
    The caller's own record, as register/login/me return it.

    Extends ``UserSummary`` with ``is_moderator`` so the client can gate the
    moderation UI. Only these self-describing endpoints use it -- the flag must
    not ride along on tweet authors, where it would make the moderator roster
    browsable.
    """

    is_moderator: bool = False


class UserLogin(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class PasswordChange(BaseModel):
    # No minimum on the current password. Bounding it would answer a short wrong
    # guess with 422 and a long wrong guess with 403, which tells an attacker
    # holding a stolen session something about the password they are guessing.
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )


class AccountDeletion(BaseModel):
    # Deleting an account is irreversible from the user's side, so -- like
    # change-password and change-email -- a stolen session cookie alone must not
    # be enough. Proving knowledge of the password is the gate. No minimum, for
    # the same timing reason PasswordChange.current_password has none.
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class EmailChange(BaseModel):
    # Requiring the current password is what stops a stolen session cookie from
    # redirecting reset mail to the thief and taking the account over. Without
    # it, change-password's own current-password check would be trivially
    # bypassable: point the address at yourself, then "forget" the password.
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    email: Email


class ForgotPassword(BaseModel):
    email: Email


class PasswordReset(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )


class EmailVerification(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class SessionOut(BaseModel):
    # `id` is sha256(sid), an opaque handle -- never the session id itself, which
    # is half of a bearer credential. It is enough to target one session for
    # revocation, and useless for anything else.
    id: str
    ip: str | None = None
    user_agent: str | None = None
    created_at: datetime
    last_seen: datetime
    # True for the session making the request, so the UI can label "this device"
    # and refuse to offer a revoke button that would just log the caller out.
    current: bool = False


class UserDiscoveryOut(UserSummary):
    is_following: bool = False
    is_current_user: bool = False


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=50)
    bio: str | None = Field(default=None, max_length=160)
    # Who may open a DM conversation with the user (see schemas/dm.py).
    dm_policy: Literal["everyone", "following", "none"] | None = None


class UserProfileOut(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    created_at: datetime
    follower_count: int
    following_count: int
    # Everything they have authored: tweets, replies, and quotes/retweets --
    # the same semantics as Twitter's and Bluesky's profile post counters.
    post_count: int
    is_following: bool
    is_current_user: bool
    # Whether this profile belongs to a deleted (tombstoned) account.
    is_deleted: bool = False
    # Whether the viewer has blocked this profile's owner.
    is_blocked: bool = False
    # Whether the viewer has muted this profile's owner. Like is_blocked, this is
    # the viewer's own state; the muted user is never told.
    is_muted: bool = False

    # Populated only when is_current_user. Every profile is world-readable, so
    # these stay None for anyone else -- and email is deliberately absent from
    # UserSummary, which rides along inside every tweet as the author.
    email: str | None = None
    pending_email: str | None = None
    # Also owner-only: the DM privacy setting, so the Messages UI can show it.
    dm_policy: Literal["everyone", "following", "none"] | None = None
    # Owner-only too: gates the moderation UI. Kept off everyone else's profile
    # so the moderator roster is not browsable -- the same non-disclosure rule
    # as the 404 on /moderation itself.
    is_moderator: bool = False
