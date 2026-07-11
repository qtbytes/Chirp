from pydantic import BaseModel

from app.schemas.user import UserDiscoveryOut


class FollowActionOut(BaseModel):
    follower_id: int
    followee_id: int
    is_following: bool


class FollowListPage(BaseModel):
    # Rows are the same shape the discovery panel renders -- each carries
    # is_following (for the follow button) and is_current_user (to hide it).
    items: list[UserDiscoveryOut]
    next_cursor: str | None = None
