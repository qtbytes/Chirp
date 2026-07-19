"""
Full-text search over post content, backed by the SQLite FTS5 ``posts_fts``
index (see ``app/db/fts.py``).

Two orderings are supported:

- ``relevance`` (default): BM25 relevance, paginated by a ``(score, id)`` keyset
  cursor. BM25 is deterministic for a given query and index, so the boundary
  row's score recomputes to the same float on the next page -- the same trick the
  "for you" rank cursor uses. SQLite's ``bm25()`` returns smaller (more negative)
  numbers for better matches, hence ascending order.
- ``recent``: newest-first by ``(created_at, id)`` -- the same keyset cursor the
  timelines use. The FTS match is applied as a subquery so the ordering runs over
  the typed ``posts.created_at`` column and the datetime cursor compares
  correctly.
"""

import re
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, bindparam, func, literal_column, or_, select, text
from sqlalchemy.orm import Session, aliased, joinedload

from app.models.post import Post
from app.models.user import User
from app.repositories import engagement_repository, tweet_repository
from app.repositories.visibility import visible_root_predicate

SearchSort = Literal["relevance", "recent"]

# Split the query into word tokens, dropping every FTS5 operator character in the
# process (quotes, parentheses, ``*``, ``:``, ``-`` ...). This is what keeps a raw
# user string from being interpreted -- or misparsed -- as an FTS match
# expression, and it is bound as a parameter besides.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def build_match(query: str) -> str | None:
    """
    Turn a user query into a safe FTS5 MATCH string, or ``None`` if it has no
    usable token (in which case the caller returns an empty page).

    Each token becomes a prefix term (``token*``) and terms are ANDed (space),
    so "fast api" matches posts containing a word starting with each -- a
    type-ahead-friendly behaviour.
    """
    tokens = _TOKEN_RE.findall(query or "")
    if not tokens:
        return None
    return " ".join(f"{token}*" for token in tokens)


def encode_search_cursor(score: float, post_id: int) -> str:
    """``score|post_id`` -- ``repr`` on the float so it round-trips exactly."""
    return f"{score!r}|{post_id}"


def decode_search_cursor(cursor: str | None) -> tuple[float | None, int | None]:
    """Decode a relevance cursor. Returns ``(None, None)`` for absent/malformed input."""
    if not cursor:
        return None, None
    try:
        score_raw, id_raw = cursor.split("|", maxsplit=1)
        return float(score_raw), int(id_raw)
    except (TypeError, ValueError):
        return None, None


def _relevance_hits(
    db: Session,
    match: str,
    limit: int,
    cursor_score: float | None,
    cursor_id: int | None,
    exclude_author_ids: set[int] | None,
    viewer_id: int,
) -> list[tuple[int, float]]:
    """Matching post ids ordered by BM25 relevance, each with its score."""
    conditions = [
        "u.deleted_at IS NULL",
        "u.suspended_at IS NULL",
        "p.taken_down_at IS NULL",
    ]
    params: dict = {"match": match, "row_limit": limit + 1, "viewer": viewer_id}
    expanding = []

    # Gate on the audience of each hit's *thread root* (``root`` below), so a
    # reply is visible exactly when its root tweet is. Mirrors
    # ``visible_root_predicate`` in raw SQL for the FTS path.
    conditions.append(
        "(root.user_id = :viewer OR root.visibility = 'public'"
        " OR (root.visibility = 'followers'"
        " AND root.user_id IN (SELECT followee_id FROM follows"
        " WHERE follower_id = :viewer)))"
    )

    if exclude_author_ids:
        conditions.append("p.user_id NOT IN :excluded")
        params["excluded"] = list(exclude_author_ids)
        expanding.append(bindparam("excluded", expanding=True))

    if cursor_score is not None and cursor_id is not None:
        conditions.append(
            "(s.score > :cur_score OR (s.score = :cur_score AND p.id > :cur_id))"
        )
        params["cur_score"] = cursor_score
        params["cur_id"] = cursor_id

    where = " AND ".join(conditions)
    sql = f"""
        SELECT s.rowid AS id, s.score AS score
        FROM (
            SELECT rowid, bm25(posts_fts) AS score
            FROM posts_fts
            WHERE posts_fts MATCH :match
        ) AS s
        JOIN posts p ON p.id = s.rowid
        JOIN users u ON u.id = p.user_id
        JOIN posts root ON root.id = COALESCE(p.root_id, p.id)
        WHERE {where}
        ORDER BY s.score ASC, p.id ASC
        LIMIT :row_limit
    """
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*expanding)

    return [(row.id, row.score) for row in db.execute(stmt, params).all()]


def _recent_hits(
    db: Session,
    match: str,
    limit: int,
    cursor_created_at: datetime | None,
    cursor_id: int | None,
    exclude_author_ids: set[int] | None,
    viewer_id: int,
) -> list[tuple[int, datetime]]:
    """Matching post ids ordered newest-first, each with its ``created_at``."""
    # The FTS match feeds an ``IN`` subquery so the ordering and the datetime
    # cursor run over the typed ``posts.created_at`` column (raw-SQL datetime
    # binding against SQLite's stored string form is unreliable).
    match_ids = (
        select(literal_column("rowid"))
        .select_from(text("posts_fts"))
        .where(text("posts_fts MATCH :match").bindparams(match=match))
    )

    # Gate on each hit's thread root so a reply is visible exactly when its root
    # tweet is (a top-level tweet is its own root via the coalesce).
    Root = aliased(Post)
    stmt = (
        select(Post.id, Post.created_at)
        .join(User, User.id == Post.user_id)
        .join(Root, Root.id == func.coalesce(Post.root_id, Post.id))
        .where(
            Post.id.in_(match_ids),
            User.deleted_at.is_(None),
            User.suspended_at.is_(None),
            Post.taken_down_at.is_(None),
            visible_root_predicate(viewer_id, Root),
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit + 1)
    )
    if exclude_author_ids:
        stmt = stmt.where(Post.user_id.not_in(exclude_author_ids))
    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Post.created_at < cursor_created_at,
                and_(
                    Post.created_at == cursor_created_at,
                    Post.id < cursor_id,
                ),
            )
        )

    return [(row.id, row.created_at) for row in db.execute(stmt).all()]


def _hydrate(
    db: Session,
    ordered: list[tuple[int, object]],
    sort: SearchSort,
    current_user_id: int,
) -> list[dict]:
    """
    Load posts + engagement for the ordered hit ids and build result rows.

    Preserves the incoming order and attaches the cursor field the caller's sort
    pages by: ``cursor_created_at`` for ``recent``, ``cursor_score`` for
    ``relevance``. Tweets and replies are stat'd with the method that matches
    their kind so each keeps its correct counts.
    """
    if not ordered:
        return []

    hit_ids = [pid for pid, _ in ordered]

    posts = db.scalars(
        select(Post).options(joinedload(Post.author)).where(Post.id.in_(hit_ids))
    ).all()
    post_by_id = {post.id: post for post in posts}

    tweet_ids = [pid for pid in hit_ids if post_by_id[pid].reply_to_id is None]
    reply_ids = [pid for pid in hit_ids if post_by_id[pid].reply_to_id is not None]

    stats_by_id: dict[int, dict] = {}
    for stats in tweet_repository.list_tweet_stats(
        db, tweet_ids=tweet_ids, current_user_id=current_user_id
    ):
        stats_by_id[stats["id"]] = stats
    for stats in engagement_repository.list_comment_stats(
        db, comment_ids=reply_ids, current_user_id=current_user_id
    ):
        stats_by_id[stats["id"]] = stats

    empty = {
        "like_count": 0,
        "comment_count": 0,
        "retweet_count": 0,
        "view_count": 0,
        "liked_by_me": False,
    }

    rows: list[dict] = []
    for pid, cursor_value in ordered:
        post = post_by_id.get(pid)
        if post is None:
            continue
        stats = stats_by_id.get(pid, empty)
        row = {
            "post": post,
            "is_reply": post.reply_to_id is not None,
            "thread_id": post.root_id or post.id,
            "like_count": stats["like_count"],
            "comment_count": stats["comment_count"],
            "retweet_count": stats["retweet_count"],
            "view_count": stats["view_count"],
            "liked_by_me": stats["liked_by_me"],
            "cursor_id": pid,
        }
        if sort == "recent":
            row["cursor_created_at"] = cursor_value
        else:
            row["cursor_score"] = cursor_value
        rows.append(row)
    return rows


def search_posts(
    db: Session,
    *,
    match: str,
    limit: int,
    sort: SearchSort = "relevance",
    current_user_id: int,
    exclude_author_ids: set[int] | None = None,
    cursor_score: float | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Return posts (tweets and replies) matching ``match``, ordered by ``sort``.

    Fetches ``limit + 1`` rows so the caller can detect a next page. Excludes
    deleted authors and, via ``exclude_author_ids``, blocked/blocking ones --
    the same visibility rules the feeds apply.
    """
    if sort == "recent":
        ordered = _recent_hits(
            db,
            match,
            limit,
            cursor_created_at,
            cursor_id,
            exclude_author_ids,
            current_user_id,
        )
    else:
        ordered = _relevance_hits(
            db,
            match,
            limit,
            cursor_score,
            cursor_id,
            exclude_author_ids,
            current_user_id,
        )

    return _hydrate(db, ordered, sort, current_user_id)
