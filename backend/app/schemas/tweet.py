from datetime import datetime
from typing import Literal

from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field


class TweetCreate(BaseModel):
    content: str = Field(min_length=1, max_length=280)


class TweetOut(BaseModel):
    id: int
    content: str
    created_at: datetime
    author: UserSummary
    like_count: int = 0
    comment_count: int = 0
    retweet_count: int = 0
    liked_by_me: bool = False

    model_config = ConfigDict(from_attributes=True)


class TimelinePage(BaseModel):
    items: list[TweetOut]
    next_cursor: str | None = None
    strategy: Literal["read", "write", "for_you"]
