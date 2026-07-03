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

    model_config = ConfigDict(from_attributes=True)
