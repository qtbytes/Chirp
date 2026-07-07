from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import engagement_repository
from app.schemas.comment import CommentCreate, CommentOut
from app.schemas.user import UserSummary
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/tweets", tags=["interactions"])


@router.post("/{tweet_id}/likes", status_code=status.HTTP_201_CREATED)
def like_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """
    Like a tweet.

    Interview points:
    - Keep the API idempotent. If the user already liked the tweet,
      return created=False instead of raising an error.
    - Validate the target tweet exists in the repository layer.
    """
    try:
        created = engagement_repository.like_tweet(
            db,
            user_id=current_user_id,
            tweet_id=tweet_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "tweet_id": tweet_id,
        "liked": True,
        "created": created,
    }


@router.delete("/{tweet_id}/likes", status_code=status.HTTP_200_OK)
def unlike_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """
    Unlike a tweet.

    Returning removed=False keeps the endpoint safe to retry and easy to
    discuss in interviews as an idempotent delete operation.
    """
    removed = engagement_repository.unlike_tweet(
        db,
        user_id=current_user_id,
        tweet_id=tweet_id,
    )
    return {
        "tweet_id": tweet_id,
        "liked": False,
        "removed": removed,
    }


@router.post("/{tweet_id}/likes/toggle", status_code=status.HTTP_200_OK)
def toggle_tweet_like(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    if engagement_repository.has_liked_tweet(
        db,
        user_id=current_user_id,
        tweet_id=tweet_id,
    ):
        engagement_repository.unlike_tweet(
            db,
            user_id=current_user_id,
            tweet_id=tweet_id,
        )
        liked = False
    else:
        try:
            engagement_repository.like_tweet(
                db,
                user_id=current_user_id,
                tweet_id=tweet_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        liked = True

    return {
        "tweet_id": tweet_id,
        "liked": liked,
        "like_count": engagement_repository.count_tweet_likes(db, tweet_id),
    }


@router.post("/{tweet_id}/retweets", status_code=status.HTTP_201_CREATED)
def retweet_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = engagement_repository.retweet_tweet(
            db,
            user_id=current_user_id,
            tweet_id=tweet_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "tweet_id": tweet_id,
        "retweeted": True,
        "created": created,
    }


@router.delete("/{tweet_id}/retweets", status_code=status.HTTP_200_OK)
def unretweet_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    removed = engagement_repository.unretweet_tweet(
        db,
        user_id=current_user_id,
        tweet_id=tweet_id,
    )
    return {
        "tweet_id": tweet_id,
        "retweeted": False,
        "removed": removed,
    }


@router.post(
    "/{tweet_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    tweet_id: int,
    payload: CommentCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> CommentOut:
    """
    Create a comment on a tweet.

    Interview points:
    - Validate the tweet and user exist.
    - Return comment + author together to avoid extra lookups later.
    """
    try:
        comment, author = engagement_repository.create_comment(
            db,
            user_id=current_user_id,
            tweet_id=tweet_id,
            content=payload.content,
            media_url=payload.media_url,
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
        media_url=comment.media_url,
        created_at=comment.created_at,
        author=UserSummary.model_validate(author),
        like_count=0,
        comment_count=0,
        retweet_count=0,
        liked_by_me=False,
    )


@router.get(
    "/{tweet_id}/comments",
    response_model=list[CommentOut],
    status_code=status.HTTP_200_OK,
)
def list_comments(
    tweet_id: int,
    limit: int = 20,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[CommentOut]:
    """
    List recent comments for a tweet.

    Interview points:
    - Repository uses a joined query to avoid N+1 when loading authors.
    - In production, this can also use cursor pagination for deep threads.
    """
    try:
        rows = engagement_repository.list_comments_by_tweet(
            db,
            tweet_id=tweet_id,
            limit=limit,
            current_user_id=current_user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return [
        CommentOut(
            id=comment.id,
            tweet_id=comment.tweet_id,
            parent_comment_id=comment.parent_comment_id,
            content=comment.content,
            media_url=comment.media_url,
            created_at=comment.created_at,
            author=UserSummary.model_validate(author),
            like_count=like_count,
            comment_count=comment_count,
            retweet_count=retweet_count,
            liked_by_me=liked_by_me,
        )
        for comment, author, like_count, comment_count, retweet_count, liked_by_me in rows
    ]
