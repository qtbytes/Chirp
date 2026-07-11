import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models.block import Block
from app.models.feed import FeedItem
from app.models.follow import Follow
from app.models.like import Like
from app.models.mute import Mute
from app.models.notification import Notification
from app.models.report import Report
from app.models.user import User


def create_user(
    db: Session, username: str, password_hash: str, email: str | None = None
) -> User:
    if db.scalar(select(User).where(User.username == username)):
        raise ValueError("username already exists")

    # The address arrives unconfirmed: it goes to pending_email, and only a
    # redeemed verification token promotes it.
    if email is not None and get_user_by_email(db, email):
        raise ValueError("email already registered")

    user = User(username=username, password_hash=password_hash, pending_email=email)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user_by_email(db: Session, email: str) -> User | None:
    """Match only *confirmed* addresses. A mere claim must not receive mail."""
    return db.scalar(select(User).where(User.email == email))


def set_pending_email(db: Session, user_id: int, email: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    user.pending_email = email
    db.commit()
    db.refresh(user)
    return user


def confirm_pending_email(db: Session, user_id: int) -> User:
    """
    Promote a claimed address to the confirmed one.

    The uniqueness check happens here, not when the claim was made: two users may
    both claim an address, and whoever confirms first owns it. The loser's claim
    then fails rather than colliding with the unique index.
    """
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")
    if user.pending_email is None:
        raise ValueError("no pending email to confirm")

    owner = get_user_by_email(db, user.pending_email)
    if owner is not None and owner.id != user_id:
        raise ValueError("email already registered")

    user.email = user.pending_email
    user.pending_email = None
    db.commit()
    db.refresh(user)
    return user


def list_users(
    db: Session,
    current_user_id: int,
    query: str | None,
    limit: int,
    exclude_user_ids: set[int] | None = None,
) -> list[tuple[User, bool]]:
    stmt = select(User).where(User.deleted_at.is_(None))
    if query:
        stmt = stmt.where(User.username.ilike(f"%{query}%"))
    # Blocked (and blocking) users do not surface in discovery.
    if exclude_user_ids:
        stmt = stmt.where(User.id.not_in(exclude_user_ids))

    users = list(db.scalars(stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit)).all())
    if not users:
        return []

    user_ids = [user.id for user in users]
    followed_ids = {
        followee_id
        for (followee_id,) in db.execute(
            select(Follow.followee_id).where(
                Follow.follower_id == current_user_id,
                Follow.followee_id.in_(user_ids),
            )
        ).all()
    }

    return [(user, user.id in followed_ids) for user in users]


def update_user_profile(db: Session, user_id: int, fields: dict) -> User:
    """Apply a partial profile update; only keys present in ``fields`` change."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    for key in ("display_name", "bio"):
        if key in fields:
            value = fields[key]
            # Treat blank input as "cleared" so an empty field falls back to
            # the username rather than showing an empty display name.
            if isinstance(value, str) and value.strip() == "":
                value = None
            setattr(user, key, value)

    db.commit()
    db.refresh(user)
    return user


def update_user_password(db: Session, user_id: int, password_hash: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    user.password_hash = password_hash
    db.commit()
    db.refresh(user)
    return user


def update_user_avatar(db: Session, user_id: int, avatar_url: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    user.avatar_url = avatar_url
    db.commit()
    db.refresh(user)
    return user


def soft_delete_user(
    db: Session, user_id: int, scrubbed_password_hash: str
) -> User | None:
    """
    Tombstone an account: clear everything keyed to the user, scrub their
    personal data, and stamp ``deleted_at`` -- all in one transaction. Returns
    the updated row, or None if the user is missing or already deleted.

    Authored posts are deliberately kept, so replies and quotes others built on
    them still resolve an author (the whole reason this is a soft delete). What
    goes: every personal edge (follows both ways, likes, blocks and mutes both
    ways, reports), the user's own home-feed rows, and their entire notification
    history as recipient and as actor. The PII on the row is nulled, the username
    is rewritten to ``deleted_<id>`` (freeing the original for reuse), and the
    password is replaced with an unknown hash so the row can never authenticate.

    Feed rows where the user is the *author* (``actor_id``) are left alone: the
    posts survive as tombstones, so they should keep resolving in the feeds they
    were fanned out to -- as should likes, notifications, and reports by other
    users that point at those posts, which stay valid because the posts stay.
    """
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        return None

    db.execute(
        delete(Follow).where(
            or_(Follow.follower_id == user_id, Follow.followee_id == user_id)
        )
    )
    db.execute(delete(Like).where(Like.user_id == user_id))
    db.execute(delete(FeedItem).where(FeedItem.owner_id == user_id))
    db.execute(
        delete(Notification).where(
            or_(Notification.user_id == user_id, Notification.actor_id == user_id)
        )
    )
    db.execute(
        delete(Block).where(
            or_(Block.blocker_id == user_id, Block.blocked_id == user_id)
        )
    )
    db.execute(
        delete(Mute).where(or_(Mute.muter_id == user_id, Mute.muted_id == user_id))
    )
    db.execute(delete(Report).where(Report.reporter_id == user_id))

    # deleted_<id> is unique by construction: the id is unique, and registration
    # forbids anyone from claiming that shape. The check is a belt-and-suspenders
    # guard for a legacy row that took the name before that rule existed -- fall
    # back to a random suffix rather than crash on the unique constraint.
    tombstone_username = f"deleted_{user_id}"
    taken = db.scalar(
        select(User.id).where(
            User.username == tombstone_username, User.id != user_id
        )
    )
    if taken is not None:
        tombstone_username = f"deleted_{user_id}_{secrets.token_hex(4)}"

    user.username = tombstone_username
    user.email = None
    user.pending_email = None
    user.display_name = None
    user.bio = None
    user.avatar_url = None
    user.password_hash = scrubbed_password_hash
    user.deleted_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(user)
    return user
