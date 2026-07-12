"""
Full-text search over post content, backed by the SQLite FTS5 ``posts_fts``
index (see ``app/db/fts.py``).

Results are ranked by BM25 relevance, not recency, so pagination uses a
``(score, id)`` keyset cursor rather than the timelines' ``(created_at, id)``
one. BM25 is deterministic for a given query and index, so the boundary row's
score recomputes to the exact same float on the next page -- the same trick the
"for you" rank cursor relies on. SQLite's ``bm25()`` returns smaller (more
negative) numbers for better matches, hence the ascending order.
"""

import re

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session, joinedload

from app.models.post import Post
from app.repositories import engagement_repository, tweet_repository

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
    """Decode a search cursor. Returns ``(None, None)`` for absent/malformed input."""
    if not cursor:
        return None, None
    try:
        score_raw, id_raw = cursor.split("|", maxsplit=1)
        return float(score_raw), int(id_raw)
    except (TypeError, ValueError):
        return None, None


def search_posts(
    db: Session,
    *,
    match: str,
    limit: int,
    cursor_score: float | None = None,
    cursor_id: int | None = None,
    current_user_id: int,
    exclude_author_ids: set[int] | None = None,
) -> list[dict]:
    """
    Return posts (tweets and replies) matching ``match`` most-relevant first.

    Fetches ``limit + 1`` rows so the caller can detect a next page. Excludes
    deleted authors and, via ``exclude_author_ids``, blocked/blocking ones --
    the same visibility rules the feeds apply.

    Each returned row carries the post, its engagement stats (stat'd with the
    method that matches its kind), ``is_reply`` / ``thread_id`` for linking, and
    ``cursor_score`` / ``cursor_id`` for the next cursor.
    """
    conditions = ["u.deleted_at IS NULL"]
    params: dict = {"match": match, "row_limit": limit + 1}
    expanding = []

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
        WHERE {where}
        ORDER BY s.score ASC, p.id ASC
        LIMIT :row_limit
    """
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*expanding)

    hits = db.execute(stmt, params).all()
    if not hits:
        return []

    hit_ids = [row.id for row in hits]
    score_by_id = {row.id: row.score for row in hits}

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
        "liked_by_me": False,
    }

    # hit_ids is already ordered by (score ASC, id ASC) from the query above.
    rows: list[dict] = []
    for pid in hit_ids:
        post = post_by_id.get(pid)
        if post is None:
            continue
        stats = stats_by_id.get(pid, empty)
        rows.append(
            {
                "post": post,
                "is_reply": post.reply_to_id is not None,
                "thread_id": post.root_id or post.id,
                "like_count": stats["like_count"],
                "comment_count": stats["comment_count"],
                "retweet_count": stats["retweet_count"],
                "liked_by_me": stats["liked_by_me"],
                "cursor_score": score_by_id[pid],
                "cursor_id": pid,
            }
        )
    return rows
