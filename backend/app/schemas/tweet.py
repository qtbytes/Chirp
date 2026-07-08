from datetime import datetime
from typing import Literal

from app.schemas.media import MAX_MEDIA_ITEMS, validate_media_urls
from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TweetCreate(BaseModel):
    content: str = Field(default="", max_length=280)
    media_urls: list[str] = Field(default_factory=list, max_length=MAX_MEDIA_ITEMS)

    @field_validator("media_urls")
    @classmethod
    def _validate_media_urls(cls, value: list[str]) -> list[str]:
        return validate_media_urls(value)

    @model_validator(mode="after")
    def _require_content_or_media(self) -> "TweetCreate":
        if not self.content.strip() and not self.media_urls:
            raise ValueError("tweet must have content or media")
        return self


class TweetOut(BaseModel):
    id: int
    content: str
    media_urls: list[str] = Field(default_factory=list)
    created_at: datetime
    edited_at: datetime | None = None
    author: UserSummary
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    liked_by_me: bool = False
    retweeted_by: UserSummary | None = None

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
