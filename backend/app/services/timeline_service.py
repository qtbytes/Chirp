from datetime import datetime
from typing import Literal

from rq.queue import Queue
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.redis_client import get_redis_client
from app.repositories import feed_repository, follow_repository, tweet_repository
from app.schemas.tweet import TimelinePage, TweetOut
from app.schemas.user import UserSummary


def encode_cursor(created_at: datetime, row_id: int) -> str:
    """
    Encode a stable cursor using (created_at, id).

    Why not offset pagination?
    - Offset gets slower on large tables.
    - Offset can skip/duplicate records when new tweets are inserted.
    - Cursor pagination is the usual interview answer for feeds/timelines.
    """
    return f"{created_at.isoformat()}|{row_id}"


def decode_cursor(cursor: str | None) -> tuple[datetime | None, int | None]:
    """
    Decode a cursor created by encode_cursor().

    Returns (None, None) for invalid inputs so the API layer can decide
    whether to reject the request.
    """
    if not cursor:
        return None, None

    try:
        created_at_raw, row_id_raw = cursor.split("|", maxsplit=1)
        return datetime.fromisoformat(created_at_raw), int(row_id_raw)
    except (TypeError, ValueError):
        return None, None


def encode_score_cursor(score: int, created_at: datetime, row_id: int) -> str:
    return f"{score}|{created_at.isoformat()}|{row_id}"


def decode_score_cursor(
    cursor: str | None,
) -> tuple[int | None, datetime | None, int | None]:
    if not cursor:
        return None, None, None

    try:
        score_raw, created_at_raw, row_id_raw = cursor.split("|", maxsplit=2)
        return int(score_raw), datetime.fromisoformat(created_at_raw), int(row_id_raw)
    except (TypeError, ValueError):
        return None, None, None


class TimelineService:
    """
    Home timeline service supporting two common interview strategies:

    1. fan-out on read:
       - query tweets from followed users at request time
       - simpler writes, heavier reads

    2. fan-out on write:
       - push tweet ids into each follower's feed at write time
       - heavier writes, very fast reads

    This service lets you discuss both approaches with the same API.
    """

    def __init__(self, db: Session):
        self.db = db
        self.redis = get_redis_client()

    def get_home_timeline(
        self,
        user_id: int,
        limit: int,
        cursor: str | None,
        strategy: Literal["read", "write"],
    ) -> TimelinePage:
        """
        Return the user's home timeline.

        Interview notes:
        - `read` strategy reads tweets from followed users directly.
        - `write` strategy reads precomputed rows from `feed_items`.
        - first page can be cached in Redis to reduce repeated reads.
        """
        cursor_created_at, cursor_id = decode_cursor(cursor)
        if cursor and (cursor_created_at is None or cursor_id is None):
            raise ValueError("invalid cursor")

        if cursor is None:
            cached = self._get_cached_page(
                user_id=user_id,
                limit=limit,
                strategy=strategy,
            )
            if cached is not None:
                return cached

        if strategy == "write":
            rows = feed_repository.list_feed_tweets(
                self.db,
                owner_id=user_id,
                limit=limit,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
            )
        else:
            followee_ids = follow_repository.list_followee_ids(
                self.db,
                follower_id=user_id,
            )
            author_ids = [user_id, *followee_ids]

            rows = tweet_repository.list_tweets_by_authors(
                self.db,
                author_ids=author_ids,
                limit=limit,
                current_user_id=user_id,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
            )

        page = self._build_page(rows=rows, limit=limit, strategy=strategy)

        if cursor is None:
            self._set_cached_page(
                user_id=user_id,
                limit=limit,
                strategy=strategy,
                page=page,
            )

        return page

    def get_for_you_timeline(
        self,
        limit: int,
        cursor: str | None,
        user_id: int | None = None,
    ) -> TimelinePage:
        cursor_score, cursor_created_at, cursor_id = decode_score_cursor(cursor)
        if cursor and (
            cursor_score is None or cursor_created_at is None or cursor_id is None
        ):
            raise ValueError("invalid cursor")

        rows = tweet_repository.list_for_you_tweets(
            self.db,
            limit=limit,
            current_user_id=user_id,
            cursor_score=cursor_score,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
        return self._build_page(rows=rows, limit=limit, strategy="for_you")

    def serialize_tweet(self, row: dict) -> TweetOut:
        """
        Convert repository row data into API schema.

        Expected row shape:
        {
            "tweet": Tweet,
            "like_count": int,
            "comment_count": int,
            "cursor_created_at": datetime,
            "cursor_id": int,
        }
        """
        tweet = row["tweet"]

        return TweetOut(
            id=tweet.id,
            content=tweet.content,
            created_at=tweet.created_at,
            author=UserSummary.model_validate(tweet.author),
            like_count=row["like_count"],
            comment_count=row["comment_count"],
            retweet_count=row["retweet_count"],
            liked_by_me=row.get("liked_by_me", False),
        )

    def _build_page(
        self,
        rows: list[dict],
        limit: int,
        strategy: Literal["read", "write", "for_you"],
    ) -> TimelinePage:
        """
        Build a timeline page and next cursor.

        Repositories fetch `limit + 1` rows so we can determine whether
        another page exists without an extra query.
        """
        has_next = len(rows) > limit
        page_rows = rows[:limit]
        items = [self.serialize_tweet(row) for row in page_rows]

        next_cursor = None
        if has_next and page_rows:
            last_row = page_rows[-1]
            if strategy == "for_you":
                next_cursor = encode_score_cursor(
                    last_row["score"],
                    last_row["cursor_created_at"],
                    last_row["cursor_id"],
                )
            else:
                next_cursor = encode_cursor(
                    last_row["cursor_created_at"],
                    last_row["cursor_id"],
                )

        return TimelinePage(
            items=items,
            next_cursor=next_cursor,
            strategy=strategy,
        )

    def _cache_key(self, user_id: int, limit: int, strategy: str) -> str:
        return f"timeline:{strategy}:user:{user_id}:limit:{limit}"

    def _get_cached_page(
        self,
        user_id: int,
        limit: int,
        strategy: str,
    ) -> TimelinePage | None:
        """
        Cache only the first page.

        This is a practical production compromise:
        - first page is hottest
        - deeper pages change often and are less frequently accessed
        """
        if self.redis is None:
            return None

        payload = self.redis.get(self._cache_key(user_id, limit, strategy))
        if not payload:
            return None

        return TimelinePage.model_validate_json(payload)

    def _set_cached_page(
        self,
        user_id: int,
        limit: int,
        strategy: str,
        page: TimelinePage,
    ) -> None:
        if self.redis is None:
            return

        self.redis.setex(
            self._cache_key(user_id, limit, strategy),
            settings.timeline_cache_ttl_seconds,
            page.model_dump_json(),
        )


def invalidate_timeline_cache_for_users(user_ids: list[int]) -> None:
    """
    Invalidate cached first-page timelines for the affected users.

    Current behavior:
    - For small fan-out sets, delete matching keys eagerly.
    - For very large fan-out sets, skip per-user Redis scans and rely on TTL.

    Why this tradeoff helps:
    - Scanning Redis once per follower is O(followers) and can dominate the job
      for celebrity fan-out.
    - This project caches only the first page and already uses a short TTL, so
      bounded staleness is acceptable for the demo benchmark.
    """
    redis_client = get_redis_client()
    if redis_client is None:
        return

    unique_user_ids = list(set(user_ids))
    eager_invalidation_limit = 1000

    if len(unique_user_ids) > eager_invalidation_limit:
        return

    for user_id in unique_user_ids:
        for strategy in ("read", "write"):
            pattern = f"timeline:{strategy}:user:{user_id}:limit:*"
            for key in redis_client.scan_iter(match=pattern):
                redis_client.delete(key)


def run_feed_fanout_job(tweet_id: int, author_id: int) -> None:
    """
    Background fan-out on write job.

    Flow:
    1. load the new tweet
    2. find all followers of the author
    3. insert one feed row per owner into `feed_items`

    Why background processing?
    - posting a tweet should stay fast
    - fan-out to many followers can be expensive
    - in production this often moves to Celery / Kafka / a queue worker

    Current implementation:
    - simple synchronous background job using a fresh DB session
    - good enough for learning and interview explanation
    """
    with SessionLocal() as db:
        tweet = tweet_repository.get_tweet(db, tweet_id)
        if tweet is None:
            return

        follower_ids = follow_repository.list_follower_ids(
            db,
            followee_id=author_id,
        )
        owner_ids = [author_id, *follower_ids]

        feed_repository.bulk_insert_feed_items(
            db,
            owner_ids=owner_ids,
            tweet_id=tweet.id,
            actor_id=author_id,
            created_at=tweet.created_at,
        )
        invalidate_timeline_cache_for_users(owner_ids)


def enqueue_feed_fanout_job(tweet_id: int, author_id: int) -> None:
    """
    Enqueue fan-out work into RQ when Redis is available.

    If Redis is unavailable, fall back to inline execution so local development
    still works.
    """
    redis_client = get_redis_client()
    if redis_client is None:
        run_feed_fanout_job(tweet_id=tweet_id, author_id=author_id)
        return

    queue = Queue(
        name=getattr(settings, "rq_queue_name", "timeline-fanout"),
        connection=redis_client,
    )
    queue.enqueue(
        "app.services.timeline_service.run_feed_fanout_job",
        tweet_id,
        author_id,
        job_timeout=settings.rq_job_timeout_seconds,
    )
