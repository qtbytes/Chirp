from app.api.deps import get_current_user_id
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.models.post import Post
from app.repositories import (
    block_repository,
    engagement_repository,
    post_repository,
    tweet_repository,
)
from app.repositories.visibility import can_view_thread
from app.schemas.comment import CommentCreate, CommentOut, CommentStatsOut
from app.schemas.user import UserSummary
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/comments", tags=["comments"])


@router.get("/stats", response_model=list[CommentStatsOut])
def list_comment_stats(
    ids: str = Query(..., min_length=1),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[CommentStatsOut]:
    try:
        comment_ids = [int(value) for value in ids.split(",") if value.strip()]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids must be comma-separated integers",
        ) from exc

    if not comment_ids or any(comment_id <= 0 for comment_id in comment_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids must contain positive integers",
        )
    if len(comment_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids supports at most 100 comments",
        )

    return [
        CommentStatsOut(**stats)
        for stats in engagement_repository.list_comment_stats(
            db,
            comment_ids=comment_ids,
            current_user_id=current_user_id,
        )
    ]


@router.get("/{comment_id}", response_model=CommentOut)
def get_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> CommentOut:
    """Load one comment with stats -- backs the comment detail page."""
    comment = tweet_repository.get_tweet(db, comment_id)
    # A taken-down comment reads as missing -- unlike a taken-down tweet there
    # is no tombstone, because the thread listing drops it with its subtree.
    if comment is None or comment.reply_to_id is None or comment.is_taken_down:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="comment not found",
        )

    # Same non-disclosure rules as GET /tweets/{id}: a block in either
    # direction and a thread audience the viewer can't see both read as 404.
    if block_repository.blocks_between(db, current_user_id, comment.author.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="comment not found",
        )
    if not can_view_thread(db, current_user_id, comment):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="comment not found",
        )

    stats_rows = engagement_repository.list_comment_stats(
        db, comment_ids=[comment_id], current_user_id=current_user_id
    )
    stats = stats_rows[0] if stats_rows else {
        "like_count": 0,
        "comment_count": 0,
        "retweet_count": 0,
        "view_count": 0,
        "liked_by_me": False,
    }
    return CommentOut(
        id=comment.id,
        tweet_id=comment.tweet_id,
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        media_urls=comment.media_urls or [],
        media_alts=comment.media_alts or [],
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        author=UserSummary.model_validate(comment.author),
        like_count=stats["like_count"],
        comment_count=stats["comment_count"],
        retweet_count=stats["retweet_count"],
        view_count=stats["view_count"],
        liked_by_me=stats["liked_by_me"],
    )


@router.post(
    "/{comment_id}/likes",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter("like"))],
)
def like_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = engagement_repository.like_comment(
            db,
            user_id=current_user_id,
            comment_id=comment_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "comment_id": comment_id,
        "liked": True,
        "created": created,
    }


@router.delete(
    "/{comment_id}/likes",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limiter("like"))],
)
def unlike_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    removed = engagement_repository.unlike_comment(
        db,
        user_id=current_user_id,
        comment_id=comment_id,
    )
    return {
        "comment_id": comment_id,
        "liked": False,
        "removed": removed,
    }


@router.post(
    "/{comment_id}/likes/toggle",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limiter("like"))],
)
def toggle_comment_like(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    if engagement_repository.has_liked_comment(
        db,
        user_id=current_user_id,
        comment_id=comment_id,
    ):
        engagement_repository.unlike_comment(
            db,
            user_id=current_user_id,
            comment_id=comment_id,
        )
        liked = False
    else:
        try:
            engagement_repository.like_comment(
                db,
                user_id=current_user_id,
                comment_id=comment_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        liked = True

    return {
        "comment_id": comment_id,
        "liked": liked,
        "like_count": engagement_repository.count_comment_likes(db, comment_id),
    }


@router.post(
    "/{comment_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter("comment"))],
)
def reply_to_comment(
    comment_id: int,
    payload: CommentCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> CommentOut:
    parent = db.get(Post, comment_id)
    if parent is None or parent.reply_to_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="comment not found",
        )

    try:
        comment, author = engagement_repository.create_comment(
            db,
            user_id=current_user_id,
            tweet_id=parent.root_id,
            parent_comment_id=comment_id,
            content=payload.content,
            media_urls=payload.media_urls,
            media_alts=payload.media_alts,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return CommentOut(
        id=comment.id,
        tweet_id=comment.tweet_id,
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        media_urls=comment.media_urls or [],
        media_alts=comment.media_alts or [],
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        author=UserSummary.model_validate(author),
        like_count=0,
        comment_count=0,
        retweet_count=0,
        view_count=0,
        liked_by_me=False,
    )


@router.patch("/{comment_id}", response_model=CommentOut)
def edit_comment(
    comment_id: int,
    payload: CommentCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> CommentOut:
    """Edit a comment's content/media. Only the author may edit."""
    parent = db.get(Post, comment_id)
    if parent is not None and parent.reply_to_id is None:
        # A top-level tweet is not a comment; edit it via /tweets.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="comment not found"
        )
    try:
        comment = post_repository.update_post(
            db,
            post_id=comment_id,
            user_id=current_user_id,
            content=payload.content,
            media_urls=payload.media_urls,
            media_alts=payload.media_alts,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="comment not found"
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you can only edit your own comments",
        )

    stats_rows = engagement_repository.list_comment_stats(
        db, comment_ids=[comment_id], current_user_id=current_user_id
    )
    stats = stats_rows[0] if stats_rows else {
        "like_count": 0,
        "comment_count": 0,
        "retweet_count": 0,
        "view_count": 0,
        "liked_by_me": False,
    }
    return CommentOut(
        id=comment.id,
        tweet_id=comment.tweet_id,
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        media_urls=comment.media_urls or [],
        media_alts=comment.media_alts or [],
        created_at=comment.created_at,
        edited_at=comment.edited_at,
        author=UserSummary.model_validate(comment.author),
        like_count=stats["like_count"],
        comment_count=stats["comment_count"],
        retweet_count=stats["retweet_count"],
        view_count=stats["view_count"],
        liked_by_me=stats["liked_by_me"],
    )


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> None:
    """
    Delete a comment and its sub-thread. Only the author may delete, and a
    taken-down comment is refused: the row is the moderation record.
    """
    parent = db.get(Post, comment_id)
    if parent is not None and parent.reply_to_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="comment not found"
        )
    try:
        post_repository.delete_post(
            db, post_id=comment_id, user_id=current_user_id
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="comment not found"
        )
    except post_repository.TakenDownError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this comment was removed by moderation and cannot be deleted",
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you can only delete your own comments",
        )
