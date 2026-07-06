from datetime import datetime

from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class CommentOut(BaseModel):
    id: int
    tweet_id: int
    parent_comment_id: int | None = None
    content: str
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
