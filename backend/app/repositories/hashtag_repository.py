"""Aggregate queries over the ``post_hashtags`` entity rows."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models.post import Post
from app.models.post_hashtag import PostHashtag
from app.models.user import User


def list_trending(
    db: Session,
    window_hours: int,
    baseline_hours: int,
    min_posts: int,
    limit: int,
) -> list[dict]:
    """
    The ``limit`` fastest-rising hashtags: ranked by velocity, not raw volume.

    For each tag we count its posts in the recent window (the last
    ``window_hours``) and in the baseline period (the ``baseline_hours`` before
    that), then score

        velocity = recent / (baseline_rate_per_window + 1)

    where ``baseline_rate_per_window`` scales the baseline count down to a single
    recent-window slice. A tag active far above its own baseline scores high; a
    steadily popular tag scores near 1. The ``+1`` smooths a brand-new tag (no
    baseline) so a single post cannot produce an unbounded score, and
    ``min_posts`` is the recent-count floor a tag must clear to be eligible.

    The returned ``post_count`` is the recent-window count -- what the client
    displays -- while velocity only orders the list. Rows from deleted accounts
    are excluded; blocks are viewer-specific and deliberately not applied, since
    trending is one figure shared (and cached) across all viewers.
    """
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(hours=window_hours)
    baseline_cutoff = now - timedelta(hours=window_hours + baseline_hours)

    recent_count = func.sum(
        case((PostHashtag.created_at >= recent_cutoff, 1), else_=0)
    ).label("recent_count")
    prior_count = func.sum(
        case(
            (
                and_(
                    PostHashtag.created_at >= baseline_cutoff,
                    PostHashtag.created_at < recent_cutoff,
                ),
                1,
            ),
            else_=0,
        )
    ).label("prior_count")

    rows = db.execute(
        select(PostHashtag.tag, recent_count, prior_count)
        .join(Post, Post.id == PostHashtag.post_id)
        .join(User, User.id == Post.user_id)
        .where(PostHashtag.created_at >= baseline_cutoff, User.deleted_at.is_(None))
        .group_by(PostHashtag.tag)
    ).all()

    scored: list[dict] = []
    for tag, recent, prior in rows:
        recent = int(recent or 0)
        prior = int(prior or 0)
        if recent < min_posts:
            continue
        baseline_rate_per_window = prior * window_hours / baseline_hours
        velocity = recent / (baseline_rate_per_window + 1.0)
        scored.append({"tag": tag, "post_count": recent, "velocity": velocity})

    # Highest velocity first; recent volume then tag break ties so the order is
    # total and stable.
    scored.sort(key=lambda row: (-row["velocity"], -row["post_count"], row["tag"]))

    return [
        {"tag": row["tag"], "post_count": row["post_count"]}
        for row in scored[:limit]
    ]
