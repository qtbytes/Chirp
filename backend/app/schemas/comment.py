from datetime import datetime

from app.schemas.media import MAX_MEDIA_ITEMS, validate_media_urls
from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommentCreate(BaseModel):
    content: str = Field(default="", max_length=1000)
    media_urls: list[str] = Field(default_factory=list, max_length=MAX_MEDIA_ITEMS)

    @field_validator("media_urls")
    @classmethod
    def _validate_media_urls(cls, value: list[str]) -> list[str]:
        return validate_media_urls(value)

    @model_validator(mode="after")
    def _require_content_or_media(self) -> "CommentCreate":
        if not self.content.strip() and not self.media_urls:
            raise ValueError("comment must have content or media")
        return self


class CommentOut(BaseModel):
    id: int
    tweet_id: int
    parent_comment_id: int | None = None
    content: str
    media_urls: list[str] = Field(default_factory=list)
    created_at: datetime
    edited_at: datetime | None = None
    author: UserSummary
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    liked_by_me: bool = False
    quoted_post: "QuotedPostOut | None" = None

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


from app.schemas.tweet import QuotedPostOut, TweetOut  # noqa: E402

CommentOut.model_rebuild()
ReplyWithParentOut.model_rebuild()
