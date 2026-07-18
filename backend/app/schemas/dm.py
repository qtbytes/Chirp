from datetime import datetime
from typing import Literal

from app.schemas.user import UserSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Who may open a DM conversation with the user. 'following' means only people
# the *recipient* follows -- the same shape as reply controls.
DmPolicy = Literal["everyone", "following", "none"]


class DmMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("content")
    @classmethod
    def _strip_and_require_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must have content")
        return stripped


class DmMessageOut(BaseModel):
    id: int
    sender_id: int
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationOut(BaseModel):
    id: int
    other_user: UserSummary
    last_message: DmMessageOut | None = None
    unread_count: int = 0
    # Viewer's own mute; the other participant never sees it.
    muted: bool = False
    # Whether the viewer has blocked the other participant, so the row's menu
    # can offer Unblock instead of Block.
    blocked: bool = False


class ConversationPage(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None = None


class ChatOut(BaseModel):
    """
    One conversation as the chat view needs it: the other participant, a page
    of messages (newest first; ``next_cursor`` pages further back), and
    whether the viewer may send right now. ``cannot_send_reason`` is a code
    the client turns into copy: 'policy' (their setting refuses you),
    'await_reply' (your one opener is out; wait for them), 'you_blocked'
    (you blocked them -- unblock to resume), or 'blocked_you' (they blocked
    you). Either block leaves the history readable; only sending ends.
    """

    other_user: UserSummary
    messages: list[DmMessageOut]
    next_cursor: str | None = None
    can_send: bool = True
    cannot_send_reason: (
        Literal["policy", "await_reply", "you_blocked", "blocked_you"] | None
    ) = None
    muted: bool = False
    # Whether the viewer has blocked the other participant (mirrors
    # cannot_send_reason == 'you_blocked', but explicit for the menu).
    blocked: bool = False


class DmUnreadCountOut(BaseModel):
    count: int
