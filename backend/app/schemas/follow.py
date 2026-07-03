from pydantic import BaseModel


class FollowActionOut(BaseModel):
    follower_id: int
    followee_id: int
    is_following: bool
