from datetime import datetime
from typing import Literal

from app.models.post import DEFAULT_VISIBILITY, TweetVisibility
from app.schemas.media import (
    MAX_MEDIA_ITEMS,
    normalize_media_alts,
    validate_media_urls,
)
from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TweetCreate(BaseModel):
    content: str = Field(default="", max_length=280)
    media_urls: list[str] = Field(default_factory=list, max_length=MAX_MEDIA_ITEMS)
    # Per-image alt text, parallel to ``media_urls``; shorter lists pad with "".
    media_alts: list[str] = Field(default_factory=list, max_length=MAX_MEDIA_ITEMS)
    quoted_post_id: int | None = None
    # Audience for this tweet. Omitted (``None``) means "use the default" on
    # create and "leave it unchanged" on edit -- so a plain content edit never
    # silently resets a restricted tweet back to public.
    visibility: TweetVisibility | None = None

    @field_validator("media_urls")
    @classmethod
    def _validate_media_urls(cls, value: list[str]) -> list[str]:
        return validate_media_urls(value)

    @model_validator(mode="after")
    def _require_content_or_media(self) -> "TweetCreate":
        # A quote may carry no text/media of its own (a plain retweet), so the
        # quoted post itself satisfies the "must say something" requirement.
        if (
            not self.content.strip()
            and not self.media_urls
            and self.quoted_post_id is None
        ):
            raise ValueError("tweet must have content or media")
        self.media_alts = normalize_media_alts(self.media_alts, len(self.media_urls))
        return self


class QuotedPostOut(BaseModel):
    """A compact view of a post embedded inside a quote tweet."""

    id: int
    content: str
    media_urls: list[str] = Field(default_factory=list)
    media_alts: list[str] = Field(default_factory=list)
    created_at: datetime
    author: UserSummary

    model_config = ConfigDict(from_attributes=True)


class TweetOut(BaseModel):
    id: int
    content: str
    media_urls: list[str] = Field(default_factory=list)
    media_alts: list[str] = Field(default_factory=list)
    created_at: datetime
    edited_at: datetime | None = None
    author: UserSummary
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    view_count: int = 0
    liked_by_me: bool = False
    quoted_post: QuotedPostOut | None = None
    visibility: TweetVisibility = DEFAULT_VISIBILITY

    model_config = ConfigDict(from_attributes=True)


class TweetStatsOut(BaseModel):
    id: int
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    view_count: int = 0
    liked_by_me: bool = False


class TimelinePage(BaseModel):
    items: list[TweetOut]
    next_cursor: str | None = None
    strategy: Literal["read", "write", "for_you"]


class ProfileTweetsPage(BaseModel):
    items: list[TweetOut]
    next_cursor: str | None = None


class HashtagPostsPage(BaseModel):
    items: list[TweetOut]
    next_cursor: str | None = None


class TrendingHashtagOut(BaseModel):
    tag: str
    post_count: int
