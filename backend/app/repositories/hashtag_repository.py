"""Aggregate queries over the ``post_hashtags`` entity rows."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models.post import Post
from app.models.post_hashtag import PostHashtag
from app.models.post_view import PostView
from app.models.user import User


def search_tags(db: Session, prefix: str, limit: int) -> list[dict]:
    """
    Tags starting with ``prefix`` (already normalised), most-used first.

    Backs the composer's ``#`` typeahead. Counts follow the trending rules:
    only public posts from live accounts, so a followers-only or private
    post can neither surface a tag here nor inflate its count. An empty
    prefix returns the most-used tags overall — what the composer shows the
    moment ``#`` is typed, before any letters narrow it.
    """
    # `prefix` is \w-only in practice, but escape LIKE's wildcards anyway so a
    # literal "%"/"_" can never widen the match.
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    post_count = func.count().label("post_count")
    rows = db.execute(
        select(PostHashtag.tag, post_count)
        .join(Post, Post.id == PostHashtag.post_id)
        .join(User, User.id == Post.user_id)
        .where(
            PostHashtag.tag.like(f"{escaped}%", escape="\\"),
            User.deleted_at.is_(None),
            Post.taken_down_at.is_(None),
            Post.visibility == "public",
        )
        .group_by(PostHashtag.tag)
        .order_by(post_count.desc(), PostHashtag.tag)
        .limit(limit)
    ).all()
    return [{"tag": tag, "post_count": int(count)} for tag, count in rows]


def list_trending(
    db: Session,
    window_hours: int,
    baseline_hours: int,
    min_posts: int,
    limit: int,
    view_weight: float = 0.0,
) -> list[dict]:
    """
    The ``limit`` fastest-rising hashtags: ranked by velocity, not raw volume.

    For each tag we measure its activity in the recent window (the last
    ``window_hours``) and in the baseline period (the ``baseline_hours`` before
    that), then score

        activity = posts + view_weight · views
        velocity = recent_activity / (baseline_rate_per_window + 1)

    Activity blends two signals: posts *using* the tag (posting velocity) and
    views landing on the tag's posts in that window (reading velocity, from the
    per-window ``post_views`` rows -- so a tag people are suddenly reading can
    trend even before many new posts use it). ``baseline_rate_per_window``
    scales the baseline activity down to a single recent-window slice. A tag
    active far above its own baseline scores high; a steadily popular tag
    scores near 1. The ``+1`` smooths a brand-new tag (no baseline) so a single
    post cannot produce an unbounded score, and ``min_posts`` is the
    recent-post floor a tag must clear to be eligible (posts, not views: views
    alone cannot make a tag with no fresh posts trend).

    The returned ``post_count`` is the recent-window post count -- what the
    client displays -- while velocity only orders the list. Rows from deleted
    accounts are excluded; blocks are viewer-specific and deliberately not
    applied, since trending is one figure shared (and cached) across all
    viewers.
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
        .where(
            PostHashtag.created_at >= baseline_cutoff,
            User.deleted_at.is_(None),
            Post.taken_down_at.is_(None),
            # Only public tweets feed trending -- a followers-only or private
            # tweet must never move (or leak into) a figure shared across all
            # viewers.
            Post.visibility == "public",
        )
        .group_by(PostHashtag.tag)
    ).all()

    # Views landing on each tag's posts, split into the same two windows by
    # when the *view* happened (post_views rows), not when the post was made.
    views_by_tag: dict[str, tuple[int, int]] = {}
    if view_weight > 0.0:
        recent_views = func.sum(
            case((PostView.created_at >= recent_cutoff, 1), else_=0)
        ).label("recent_views")
        prior_views = func.sum(
            case(
                (
                    and_(
                        PostView.created_at >= baseline_cutoff,
                        PostView.created_at < recent_cutoff,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("prior_views")
        view_rows = db.execute(
            select(PostHashtag.tag, recent_views, prior_views)
            .select_from(PostView)
            .join(PostHashtag, PostHashtag.post_id == PostView.post_id)
            .join(Post, Post.id == PostView.post_id)
            .join(User, User.id == Post.user_id)
            .where(
                PostView.created_at >= baseline_cutoff,
                User.deleted_at.is_(None),
                Post.taken_down_at.is_(None),
                Post.visibility == "public",
            )
            .group_by(PostHashtag.tag)
        ).all()
        views_by_tag = {
            tag: (int(recent or 0), int(prior or 0))
            for tag, recent, prior in view_rows
        }

    scored: list[dict] = []
    for tag, recent, prior in rows:
        recent = int(recent or 0)
        prior = int(prior or 0)
        if recent < min_posts:
            continue
        recent_view_count, prior_view_count = views_by_tag.get(tag, (0, 0))
        recent_activity = recent + view_weight * recent_view_count
        prior_activity = prior + view_weight * prior_view_count
        baseline_rate_per_window = prior_activity * window_hours / baseline_hours
        velocity = recent_activity / (baseline_rate_per_window + 1.0)
        scored.append({"tag": tag, "post_count": recent, "velocity": velocity})

    # Highest velocity first; recent volume then tag break ties so the order is
    # total and stable.
    scored.sort(key=lambda row: (-row["velocity"], -row["post_count"], row["tag"]))

    return [
        {"tag": row["tag"], "post_count": row["post_count"]}
        for row in scored[:limit]
    ]
