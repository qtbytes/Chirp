from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.follow import Follow
from app.models.user import User


def create_user(db: Session, username: str, password_hash: str) -> User:
    existing = db.scalar(select(User).where(User.username == username))
    if existing:
        raise ValueError("username already exists")

    user = User(username=username, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def list_users(
    db: Session,
    current_user_id: int,
    query: str | None,
    limit: int,
) -> list[tuple[User, bool]]:
    stmt = select(User)
    if query:
        stmt = stmt.where(User.username.ilike(f"%{query}%"))

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
