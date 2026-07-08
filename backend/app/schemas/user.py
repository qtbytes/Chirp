from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    }
)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)

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
    password: str = Field(min_length=8, max_length=128)


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
