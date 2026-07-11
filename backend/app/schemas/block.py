from datetime import datetime

from pydantic import BaseModel

from app.schemas.user import UserSummary


class BlockActionOut(BaseModel):
    blocked_id: int
    is_blocked: bool


class BlockedUserOut(UserSummary):
    blocked_at: datetime


class BlockListPage(BaseModel):
    items: list[BlockedUserOut]
    next_cursor: str | None = None
