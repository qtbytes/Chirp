from datetime import datetime

from pydantic import BaseModel

from app.schemas.user import UserSummary


class MuteActionOut(BaseModel):
    muted_id: int
    is_muted: bool


class MutedUserOut(UserSummary):
    muted_at: datetime


class MuteListPage(BaseModel):
    items: list[MutedUserOut]
    next_cursor: str | None = None
