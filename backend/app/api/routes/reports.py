from app.api.deps import get_current_user_id
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.repositories import block_repository, report_repository, tweet_repository
from app.schemas.report import ReportCreate, ReportOut
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "/posts/{post_id}",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter("report"))],
)
def report_post(
    post_id: int,
    payload: ReportCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ReportOut:
    """
    Report a post (tweet or comment) for a moderator to review.

    Idempotent per (reporter, post): re-reporting amends the reason rather than
    stacking rows. Reporting your own post is meaningless and rejected; a post
    you can't see -- because a block stands between you and its author -- is 404,
    so the block is never disclosed.
    """
    post = tweet_repository.get_tweet(db, post_id)
    # A post a moderator already took down reads as missing: it is hidden from
    # the reporter anyway, and the report it would file is already answered.
    if post is None or post.is_taken_down:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="post not found",
        )

    if post.author.id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="you cannot report your own post",
        )

    if block_repository.blocks_between(db, current_user_id, post.author.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="post not found",
        )

    report = report_repository.create_report(
        db,
        reporter_id=current_user_id,
        post_id=post_id,
        reason=payload.reason,
        details=payload.details,
    )
    return ReportOut(
        id=report.id,
        post_id=report.post_id,
        reason=report.reason,
        created_at=report.created_at,
    )
