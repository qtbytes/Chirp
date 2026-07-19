from datetime import datetime, timezone

from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.models.post import Post
from app.models.report import Report
from app.models.user import User


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

    Amending also *reopens* a resolved report: the reporter is saying the
    problem persists (perhaps after a restore), and a complaint that could
    never re-enter the queue would be silently unanswerable.
    """
    existing = db.scalar(
        select(Report).where(
            Report.reporter_id == reporter_id, Report.post_id == post_id
        )
    )
    if existing is not None:
        existing.reason = reason
        existing.details = details
        existing.status = "open"
        existing.resolved_at = None
        existing.resolved_by = None
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


# --- moderation ------------------------------------------------------------
#
# The queue's unit of work is the *post*, not the report row: dedupe-per-
# reporter means each row is one person's complaint, and a moderator judges
# the post once for all of them. Every action below therefore resolves all of
# a post's open reports together.


def list_report_queue(
    db: Session,
    status: str = "open",
    limit: int = 20,
    cursor_latest_at: datetime | None = None,
    cursor_post_id: int | None = None,
) -> list[dict]:
    """
    Reported posts grouped by post, newest report first.

    ``status`` is ``open`` (the working queue) or ``resolved`` (judged posts,
    for review and for undoing a takedown). Pages by ``(latest_at, post_id)``
    like every other feed; fetches ``limit + 1`` so the caller can detect the
    next page.

    Deliberately no block or visibility filtering: a moderator judges reported
    content regardless of their personal block list or the post's audience.
    """
    status_predicate = (
        Report.status == "open" if status == "open" else Report.status != "open"
    )
    latest_at = func.max(Report.created_at).label("latest_at")

    agg_stmt = (
        select(
            Report.post_id.label("post_id"),
            func.count().label("report_count"),
            latest_at,
        )
        .where(status_predicate)
        .group_by(Report.post_id)
        .order_by(desc("latest_at"), Report.post_id.desc())
        .limit(limit + 1)
    )
    if cursor_latest_at is not None and cursor_post_id is not None:
        agg_stmt = agg_stmt.having(
            or_(
                func.max(Report.created_at) < cursor_latest_at,
                and_(
                    func.max(Report.created_at) == cursor_latest_at,
                    Report.post_id < cursor_post_id,
                ),
            )
        )

    agg_rows = db.execute(agg_stmt).all()
    post_ids = [row.post_id for row in agg_rows]
    if not post_ids:
        return []

    posts_by_id = {
        post.id: post
        for post in db.scalars(
            select(Post)
            .options(joinedload(Post.author))
            .where(Post.id.in_(post_ids))
        ).all()
    }

    report_rows = db.execute(
        select(Report, User)
        .join(User, User.id == Report.reporter_id)
        .where(Report.post_id.in_(post_ids), status_predicate)
        .order_by(Report.created_at.desc(), Report.id.desc())
    ).all()
    reports_by_post: dict[int, list[tuple[Report, User]]] = {}
    for report, reporter in report_rows:
        reports_by_post.setdefault(report.post_id, []).append((report, reporter))

    queue: list[dict] = []
    for row in agg_rows:
        post = posts_by_id.get(row.post_id)
        if post is None:
            # The post was hard-deleted by its author after being reported;
            # nothing is left to judge.
            continue
        queue.append(
            {
                "post": post,
                "report_count": int(row.report_count),
                "latest_at": row.latest_at,
                "reports": reports_by_post.get(row.post_id, []),
            }
        )
    return queue


def _resolve_open_reports(
    db: Session, post_id: int, resolution: str, moderator_id: int
) -> int:
    """Close every open report on the post; returns how many were closed."""
    result = db.execute(
        update(Report)
        .where(Report.post_id == post_id, Report.status == "open")
        .values(
            status=resolution,
            resolved_at=datetime.now(timezone.utc),
            resolved_by=moderator_id,
        )
    )
    return int(result.rowcount or 0)


def dismiss_reports(db: Session, post_id: int, moderator_id: int) -> int:
    """Nothing wrong with the post: close its open reports as ``dismissed``."""
    resolved = _resolve_open_reports(db, post_id, "dismissed", moderator_id)
    db.commit()
    return resolved


def take_down_post(db: Session, post: Post, moderator_id: int) -> int:
    """
    Hide the post and close its open reports as ``actioned``, in one
    transaction. Idempotent: a second takedown re-resolves nothing and keeps
    the original timestamp.
    """
    if post.taken_down_at is None:
        post.taken_down_at = datetime.now(timezone.utc)
    resolved = _resolve_open_reports(db, post.id, "actioned", moderator_id)
    db.commit()
    return resolved


def restore_post(db: Session, post: Post) -> None:
    """
    Undo a takedown. The resolved reports stay resolved -- the judgement
    happened; this only reverses its effect on the post.
    """
    post.taken_down_at = None
    db.commit()
