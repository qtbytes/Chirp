from datetime import datetime, timezone

from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.post import Post
from app.models.report import Report
from app.models.user import User


def _find_existing_report(
    db: Session,
    reporter_id: int,
    post_id: int | None,
    reported_user_id: int | None,
) -> Report | None:
    # ``== None`` renders as IS NULL, so this matches only the caller's target
    # kind: a user's post reports never collide with their report of its author.
    return db.scalar(
        select(Report).where(
            Report.reporter_id == reporter_id,
            Report.post_id == post_id,
            Report.reported_user_id == reported_user_id,
        )
    )


def _amend_report(report: Report, reason: str, details: str | None) -> None:
    """
    Overwrite a report with the latest complaint and (re)open it.

    ``created_at`` is bumped to now. The queue orders targets by their newest
    report (``max(created_at)``), so without this a re-report -- especially one
    reopening a long-resolved report -- would re-enter the queue at its stale
    original position, buried where a moderator working newest-first might never
    reach it. Bumping it resurfaces the target, which is the whole point of
    re-reporting.
    """
    report.reason = reason
    report.details = details
    report.status = "open"
    report.resolved_at = None
    report.resolved_by = None
    report.created_at = datetime.now(timezone.utc)


def _upsert_report(
    db: Session,
    reporter_id: int,
    reason: str,
    details: str | None,
    *,
    post_id: int | None = None,
    reported_user_id: int | None = None,
) -> Report:
    """
    Record a report, idempotently per (reporter, target).

    Re-reporting the same target is treated as amending the earlier report --
    the reason and details are overwritten rather than inserting a duplicate
    row -- so a moderator sees one entry per reporter, carrying their latest
    complaint.

    Amending also *reopens* a resolved report: the reporter is saying the
    problem persists (perhaps after a restore or an unsuspension), and a
    complaint that could never re-enter the queue would be silently
    unanswerable.

    The insert is guarded against a race: two reports from the same reporter on
    the same target can both miss the existence check and race to INSERT, and
    the loser hits the ``uq_report_reporter_*`` unique constraint. Rather than
    surface that as a 500, the loser rolls back and falls through to the amend
    path -- the row the winner created is exactly what an amend would have
    updated, so the two orderings converge on one row carrying the later
    complaint.
    """
    existing = _find_existing_report(db, reporter_id, post_id, reported_user_id)
    if existing is not None:
        _amend_report(existing, reason, details)
        db.commit()
        return existing

    report = Report(
        reporter_id=reporter_id,
        post_id=post_id,
        reported_user_id=reported_user_id,
        reason=reason,
        details=details,
    )
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _find_existing_report(db, reporter_id, post_id, reported_user_id)
        if existing is None:
            # The unique constraint fired but no row is there to amend -- not the
            # race we handle. Re-raise rather than silently swallow it.
            raise
        _amend_report(existing, reason, details)
        db.commit()
        return existing
    return report


def create_report(
    db: Session,
    reporter_id: int,
    post_id: int,
    reason: str,
    details: str | None = None,
) -> Report:
    """Report a post. See ``_upsert_report`` for the amend/reopen semantics."""
    return _upsert_report(db, reporter_id, reason, details, post_id=post_id)


def create_user_report(
    db: Session,
    reporter_id: int,
    reported_user_id: int,
    reason: str,
    details: str | None = None,
) -> Report:
    """Report an account. See ``_upsert_report`` for the amend/reopen semantics."""
    return _upsert_report(
        db, reporter_id, reason, details, reported_user_id=reported_user_id
    )


# --- moderation ------------------------------------------------------------
#
# The queue's unit of work is the *target* -- a post or an account -- not the
# report row: dedupe-per-reporter means each row is one person's complaint,
# and a moderator judges the target once for all of them. Every action below
# therefore resolves all of a target's open reports together.

# One integer keys both target kinds: post ids as themselves (positive),
# reported-user ids negated. Exactly one side is set per row, so the key is
# total and collision-free, and the queue keeps the plain (timestamp, int)
# cursor every other feed uses.
_target_key = func.coalesce(Report.post_id, -Report.reported_user_id)


def list_report_queue(
    db: Session,
    status: str = "open",
    limit: int = 20,
    cursor_latest_at: datetime | None = None,
    cursor_target_key: int | None = None,
) -> list[dict]:
    """
    Reported targets grouped by target, newest report first. Each returned row
    has exactly one of ``post`` / ``reported_user`` set, plus ``target_key``
    for the cursor.

    ``status`` is ``open`` (the working queue) or ``resolved`` (judged targets,
    for review and for undoing a takedown or suspension). Pages by
    ``(latest_at, target_key)``; fetches ``limit + 1`` so the caller can detect
    the next page.

    Deliberately no block or visibility filtering: a moderator judges reported
    content regardless of their personal block list or the post's audience.
    """
    status_predicate = (
        Report.status == "open" if status == "open" else Report.status != "open"
    )
    # Exclude reports whose post is gone (legacy rows from before delete_post
    # cascaded reports; users only soft-delete, so user targets always exist).
    # This must happen in SQL, not by skipping after the fetch: skipped rows
    # would still consume the LIMIT window, shrinking pages and ending
    # pagination early while real queue items sit unreachable beyond it.
    target_exists = or_(
        Report.post_id.is_(None),
        select(Post.id).where(Post.id == Report.post_id).exists(),
    )
    latest_at = func.max(Report.created_at).label("latest_at")

    agg_stmt = (
        select(
            _target_key.label("target_key"),
            func.count().label("report_count"),
            latest_at,
        )
        .where(status_predicate, target_exists)
        .group_by(_target_key)
        .order_by(desc("latest_at"), desc("target_key"))
        .limit(limit + 1)
    )
    if cursor_latest_at is not None and cursor_target_key is not None:
        agg_stmt = agg_stmt.having(
            or_(
                func.max(Report.created_at) < cursor_latest_at,
                and_(
                    func.max(Report.created_at) == cursor_latest_at,
                    _target_key < cursor_target_key,
                ),
            )
        )

    agg_rows = db.execute(agg_stmt).all()
    post_ids = [row.target_key for row in agg_rows if row.target_key > 0]
    user_ids = [-row.target_key for row in agg_rows if row.target_key < 0]
    if not post_ids and not user_ids:
        return []

    posts_by_id = {
        post.id: post
        for post in db.scalars(
            select(Post)
            .options(joinedload(Post.author))
            .where(Post.id.in_(post_ids))
        ).all()
    } if post_ids else {}
    users_by_id = {
        user.id: user
        for user in db.scalars(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}

    target_predicates = []
    if post_ids:
        target_predicates.append(Report.post_id.in_(post_ids))
    if user_ids:
        target_predicates.append(Report.reported_user_id.in_(user_ids))
    report_rows = db.execute(
        select(Report, User)
        .join(User, User.id == Report.reporter_id)
        .where(or_(*target_predicates), status_predicate)
        .order_by(Report.created_at.desc(), Report.id.desc())
    ).all()
    reports_by_key: dict[int, list[tuple[Report, User]]] = {}
    for report, reporter in report_rows:
        key = report.post_id if report.post_id is not None else -report.reported_user_id
        reports_by_key.setdefault(key, []).append((report, reporter))

    queue: list[dict] = []
    for row in agg_rows:
        key = row.target_key
        post = posts_by_id.get(key) if key > 0 else None
        reported_user = users_by_id.get(-key) if key < 0 else None
        if post is None and reported_user is None:
            # Unreachable while ``target_exists`` holds (and delete_post
            # discards reports with the post); kept so a stray row degrades to
            # a missing card rather than a 500. (Accounts only soft-delete,
            # and stay listed so their open reports can still be dismissed.)
            continue
        queue.append(
            {
                "target_key": key,
                "post": post,
                "reported_user": reported_user,
                "report_count": int(row.report_count),
                "latest_at": row.latest_at,
                "reports": reports_by_key.get(key, []),
            }
        )
    return queue


def _resolve_open_reports(
    db: Session, target_predicate, resolution: str, moderator_id: int
) -> int:
    """Close every open report on the target; returns how many were closed."""
    result = db.execute(
        update(Report)
        .where(target_predicate, Report.status == "open")
        .values(
            status=resolution,
            resolved_at=datetime.now(timezone.utc),
            resolved_by=moderator_id,
        )
    )
    return int(result.rowcount or 0)


def dismiss_reports(db: Session, post_id: int, moderator_id: int) -> int:
    """Nothing wrong with the post: close its open reports as ``dismissed``."""
    resolved = _resolve_open_reports(
        db, Report.post_id == post_id, "dismissed", moderator_id
    )
    db.commit()
    return resolved


def dismiss_user_reports(db: Session, user_id: int, moderator_id: int) -> int:
    """Nothing wrong with the account: close its open reports as ``dismissed``."""
    resolved = _resolve_open_reports(
        db, Report.reported_user_id == user_id, "dismissed", moderator_id
    )
    db.commit()
    return resolved


def resolve_user_reports(db: Session, user_id: int, moderator_id: int) -> int:
    """
    Close the account's open reports as ``actioned``; the suspension answered
    them. Stages only -- the caller commits, so the resolutions land in the
    same transaction as the suspension stamp.

    Unlike a takedown, no notifications: the notification dedupe key has no
    user-target column (a second "account you reported was actioned" notice
    would be swallowed as a repeat of the first), and the outcome is already
    public -- the profile shows the suspension.
    """
    return _resolve_open_reports(
        db, Report.reported_user_id == user_id, "actioned", moderator_id
    )


def take_down_post(db: Session, post: Post, moderator_id: int) -> int:
    """
    Hide the post and close its open reports as ``actioned``, in one
    transaction. Idempotent: a second takedown re-resolves nothing and keeps
    the original timestamp.

    Both sides of the judgement are notified in the same commit: the author
    that their post was removed (unless the author has since deleted their
    account -- a tombstone has no notification inbox), and each open reporter
    that their report was acted on. The notifications carry the recipient as
    their own actor (``allow_self``) so the moderator is never named, and the
    usual dedupe means a takedown -> restore -> takedown cycle does not
    re-notify.
    """
    from app.repositories.notification_repository import add_notification

    reporter_ids = list(
        db.scalars(
            select(Report.reporter_id)
            .where(Report.post_id == post.id, Report.status == "open")
            .distinct()
        )
    )

    if post.taken_down_at is None:
        post.taken_down_at = datetime.now(timezone.utc)
        # A deleted author is a tombstone: their notification history was
        # scrubbed on deletion and they can never sign in to read a new notice,
        # so minting one only leaves a stray row. A *suspended* author still gets
        # it -- suspension is reversible, so the notice should be waiting when
        # they return.
        author = db.get(User, post.user_id)
        if author is not None and not author.is_deleted:
            add_notification(
                db,
                recipient_id=post.user_id,
                actor_id=post.user_id,
                type="post_removed",
                post_id=post.id,
                allow_self=True,
            )
    for reporter_id in reporter_ids:
        add_notification(
            db,
            recipient_id=reporter_id,
            actor_id=reporter_id,
            type="report_actioned",
            post_id=post.id,
            allow_self=True,
        )

    resolved = _resolve_open_reports(
        db, Report.post_id == post.id, "actioned", moderator_id
    )
    db.commit()
    return resolved


def restore_post(db: Session, post: Post) -> None:
    """
    Undo a takedown. The resolved reports stay resolved -- the judgement
    happened; this only reverses its effect on the post.
    """
    post.taken_down_at = None
    db.commit()
