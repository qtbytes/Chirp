from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    id: int
    post_id: int
    reason: ReportReason
    created_at: datetime
