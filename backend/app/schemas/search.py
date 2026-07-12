from datetime import datetime

from app.models.post import DEFAULT_VISIBILITY, TweetVisibility
from app.schemas.tweet import QuotedPostOut
from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field


class SearchPostOut(BaseModel):
    """
    A content-search hit. Shaped like ``TweetOut`` so a top-level result can be
    rendered by the same card, with two extra fields so the client can handle a
    reply too: ``is_reply`` and ``thread_id`` (the tweet whose thread to open).
    """

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
    quoted_post: QuotedPostOut | None = None
    visibility: TweetVisibility = DEFAULT_VISIBILITY
    is_reply: bool = False
    thread_id: int

    model_config = ConfigDict(from_attributes=True)


class SearchPage(BaseModel):
    items: list[SearchPostOut]
    next_cursor: str | None = None
