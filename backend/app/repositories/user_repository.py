from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.follow import Follow
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
    stmt = select(User)
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
