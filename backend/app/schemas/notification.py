from datetime import datetime

from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    id: int
    # like | retweet (a quote of the recipient's post) | comment | reply | follow
    type: str
    actor: UserSummary
    tweet_id: int | None = None
    comment_id: int | None = None
    preview: str | None = None
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnreadCountOut(BaseModel):
    count: int
