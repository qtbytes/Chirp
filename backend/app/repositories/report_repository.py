from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.report import Report


def create_report(
    db: Session,
    reporter_id: int,
    post_id: int,
    reason: str,
    details: str | None = None,
) -> Report:
    """
    Record a report, idempotently per (reporter, post).

    Re-reporting the same post is treated as amending the earlier report -- the
    reason and details are overwritten rather than inserting a duplicate row --
    so a moderator sees one entry per reporter, carrying their latest complaint.
    """
    existing = db.scalar(
        select(Report).where(
            Report.reporter_id == reporter_id, Report.post_id == post_id
        )
    )
    if existing is not None:
        existing.reason = reason
        existing.details = details
        db.commit()
        return existing

    report = Report(
        reporter_id=reporter_id,
        post_id=post_id,
        reason=reason,
        details=details,
    )
    db.add(report)
    db.commit()
    return report
