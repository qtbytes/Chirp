from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.comment import Comment
from app.models.like import Like
from app.models.tweet import Tweet
from app.models.user import User


def like_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    """
    Create a like record for a tweet.

    Returns:
    - True: a new like was created
    - False: the user had already liked the tweet

    Interview points:
    - Keep the operation idempotent.
    - Validate the target tweet exists.
    - In production, combine this with a unique constraint and possibly Redis counters.
    """
    tweet = db.get(Tweet, tweet_id)
    if tweet is None:
        raise ValueError("tweet not found")

    existing = db.scalar(
        select(Like).where(
            Like.user_id == user_id,
            Like.tweet_id == tweet_id,
        )
    )
    if existing:
        return False

    like = Like(user_id=user_id, tweet_id=tweet_id)
    db.add(like)
    db.commit()
    return True


def unlike_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    """
    Remove a like from a tweet.

    Returns:
    - True: a like existed and was removed
    - False: no like existed
    """
    existing = db.scalar(
        select(Like).where(
            Like.user_id == user_id,
            Like.tweet_id == tweet_id,
        )
    )
    if existing is None:
        return False

    db.delete(existing)
    db.commit()
    return True


def create_comment(
    db: Session,
    user_id: int,
    tweet_id: int,
    content: str,
) -> tuple[Comment, User]:
    """
    Create a comment on a tweet and return the comment with its author.

    Interview points:
    - Validate the tweet exists before inserting.
    - Return the author together to avoid extra lookups in upper layers.
    """
    tweet = db.get(Tweet, tweet_id)
    if tweet is None:
        raise ValueError("tweet not found")

    author = db.get(User, user_id)
    if author is None:
        raise ValueError("user not found")

    comment = Comment(
        user_id=user_id,
        tweet_id=tweet_id,
        content=content,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    return comment, author


def list_comments_by_tweet(
    db: Session,
    tweet_id: int,
    limit: int = 20,
) -> list[tuple[Comment, User]]:
    """
    Return recent comments for a tweet with comment authors.

    This is useful in interviews to explain how to avoid N+1 queries:
    fetch comments and users in one joined query instead of querying each
    author separately in a loop.
    """
    tweet = db.get(Tweet, tweet_id)
    if tweet is None:
        raise ValueError("tweet not found")

    stmt = (
        select(Comment, User)
        .join(User, User.id == Comment.user_id)
        .where(Comment.tweet_id == tweet_id)
        .order_by(Comment.created_at.desc(), Comment.id.desc())
        .limit(limit)
    )

    return [(comment, user) for comment, user in db.execute(stmt).all()]
