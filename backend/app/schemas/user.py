from datetime import datetime
from typing import Annotated

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
        if value.lower() in RESERVED_USERNAMES:
            raise ValueError("username is reserved")
        return value


class UserSummary(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    created_at: datetime
    avatar_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


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


class UserDiscoveryOut(UserSummary):
    is_following: bool = False
    is_current_user: bool = False


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=50)
    bio: str | None = Field(default=None, max_length=160)


class UserProfileOut(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    created_at: datetime
    follower_count: int
    following_count: int
    tweet_count: int
    is_following: bool
    is_current_user: bool

    # Populated only when is_current_user. Every profile is world-readable, so
    # these stay None for anyone else -- and email is deliberately absent from
    # UserSummary, which rides along inside every tweet as the author.
    email: str | None = None
    pending_email: str | None = None
