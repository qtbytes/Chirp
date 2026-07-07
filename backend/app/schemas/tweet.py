from datetime import datetime
from typing import Literal

from app.schemas.media import MEDIA_URL_PATTERN
from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TweetCreate(BaseModel):
    content: str = Field(default="", max_length=280)
    media_url: str | None = Field(default=None, max_length=200)

    @field_validator("media_url")
    @classmethod
    def _validate_media_url(cls, value: str | None) -> str | None:
        if value is not None and not MEDIA_URL_PATTERN.fullmatch(value):
            raise ValueError("invalid media_url")
        return value

    @model_validator(mode="after")
    def _require_content_or_media(self) -> "TweetCreate":
        if not self.content.strip() and not self.media_url:
            raise ValueError("tweet must have content or media")
        return self


class TweetOut(BaseModel):
    id: int
    content: str
    media_url: str | None = None
    created_at: datetime
    author: UserSummary
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    liked_by_me: bool = False

    model_config = ConfigDict(from_attributes=True)


class TweetStatsOut(BaseModel):
    id: int
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    liked_by_me: bool = False


class TimelinePage(BaseModel):
    items: list[TweetOut]
    next_cursor: str | None = None
    strategy: Literal["read", "write", "for_you"]


class ProfileTweetsPage(BaseModel):
    items: list[TweetOut]
    next_cursor: str | None = None
