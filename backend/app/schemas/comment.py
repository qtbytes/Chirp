from datetime import datetime

from app.schemas.media import MEDIA_URL_PATTERN
from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommentCreate(BaseModel):
    content: str = Field(default="", max_length=1000)
    media_url: str | None = Field(default=None, max_length=200)

    @field_validator("media_url")
    @classmethod
    def _validate_media_url(cls, value: str | None) -> str | None:
        if value is not None and not MEDIA_URL_PATTERN.fullmatch(value):
            raise ValueError("invalid media_url")
        return value

    @model_validator(mode="after")
    def _require_content_or_media(self) -> "CommentCreate":
        if not self.content.strip() and not self.media_url:
            raise ValueError("comment must have content or media")
        return self


class CommentOut(BaseModel):
    id: int
    tweet_id: int
    parent_comment_id: int | None = None
    content: str
    media_url: str | None = None
    created_at: datetime
    author: UserSummary
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    liked_by_me: bool = False

    model_config = ConfigDict(from_attributes=True)


class CommentStatsOut(BaseModel):
    id: int
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    liked_by_me: bool = False


class ReplyWithParentOut(BaseModel):
    comment: CommentOut
    parent_tweet: "TweetOut"


class ProfileRepliesPage(BaseModel):
    items: list[ReplyWithParentOut]
    next_cursor: str | None = None


from app.schemas.tweet import TweetOut  # noqa: E402

ReplyWithParentOut.model_rebuild()
