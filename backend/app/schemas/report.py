from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.user import UserSummary

# The categories a reporter can choose. Validated at the edge so an unknown
# reason is a 422 rather than a row the moderator can't make sense of.
ReportReason = Literal[
    "spam",
    "abuse",
    "hate",
    "violence",
    "sensitive",
    "misinformation",
    "other",
]


class ReportCreate(BaseModel):
    reason: ReportReason
    details: str | None = Field(default=None, max_length=280)


class ReportOut(BaseModel):
    """Exactly one of ``post_id`` / ``reported_user_id`` is set."""

    id: int
    post_id: int | None = None
    reported_user_id: int | None = None
    reason: ReportReason
    created_at: datetime


# --- moderation ------------------------------------------------------------

ReportStatus = Literal["open", "dismissed", "actioned"]


class ModerationReportOut(BaseModel):
    """One reporter's complaint, as shown to a moderator."""

    id: int
    reporter: UserSummary
    reason: ReportReason
    details: str | None = None
    created_at: datetime
    status: ReportStatus


class ModerationPostOut(BaseModel):
    """
    The reported post as the moderator sees it: content unmasked even when
    already taken down (the judgement needs the evidence), plus enough shape
    (``is_reply`` / ``thread_id``) for the UI to link into its thread.
    """

    id: int
    content: str
    media_urls: list[str] = Field(default_factory=list)
    created_at: datetime
    author: UserSummary
    is_reply: bool = False
    thread_id: int
    taken_down: bool = False


class ModerationQueueItem(BaseModel):
    """
    One judged-together target. Exactly one of ``post`` / ``reported_user`` is
    set: a reported post carries its evidence inline; a reported account is the
    evidence -- the moderator follows the profile link.
    """

    post: ModerationPostOut | None = None
    reported_user: UserSummary | None = None
    report_count: int
    latest_report_at: datetime
    reports: list[ModerationReportOut]


class ModerationQueuePage(BaseModel):
    items: list[ModerationQueueItem]
    next_cursor: str | None = None


class ModerationActionOut(BaseModel):
    post_id: int
    taken_down: bool
    # How many open reports this action closed. 0 on a repeat of the same
    # action -- the endpoints are idempotent.
    resolved_reports: int


class ModerationUserActionOut(BaseModel):
    user_id: int
    suspended: bool
    # How many open reports about the account this action closed (suspend
    # actions them, dismiss dismisses them; unsuspend closes nothing).
    resolved_reports: int = 0
