from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Post(Base):
    """
    Unified content model: a top-level tweet and a comment/reply are the same
    thing — a post — distinguished only by whether it replies to something.

    - ``reply_to_id`` is NULL for a top-level tweet; otherwise it points at the
      post being replied to (a tweet for a top-level comment, another comment
      for a nested reply).
    - ``root_id`` is the thread's origin tweet (a top-level post's own id). It
      keeps "all replies under tweet X" a single indexed lookup.
    """

    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_user_created", "user_id", "created_at"),
        Index("ix_posts_root_created", "root_id", "created_at"),
        Index("ix_posts_reply_created", "reply_to_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    media_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reply_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id"), nullable=True
    )
    root_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"), nullable=True)
    # When set, this post is a quote (Twitter-style): a normal top-level post
    # that embeds another post. A "retweet" is just a quote with empty content.
    quoted_post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id"), nullable=True
    )

    author = relationship("User", back_populates="posts")
    quoted_post = relationship(
        "Post",
        remote_side=[id],
        foreign_keys=[quoted_post_id],
        viewonly=True,
    )

    @property
    def is_reply(self) -> bool:
        return self.reply_to_id is not None

    # Backward-compatible aliases so tweet/comment-shaped API code keeps working
    # against the unified model without change.
    @property
    def tweet_id(self) -> int | None:
        """The thread's origin tweet id (what a comment used to store)."""
        return self.root_id

    @property
    def parent_comment_id(self) -> int | None:
        """
        The parent comment id, or None for a top-level comment.

        A top-level comment replies to the tweet itself, so its ``reply_to_id``
        equals ``root_id`` — that case maps back to "no parent comment".
        """
        if self.reply_to_id is not None and self.reply_to_id != self.root_id:
            return self.reply_to_id
        return None
