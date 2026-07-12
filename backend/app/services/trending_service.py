"""
Trending hashtags with a cache-aside layer.

Trending is expensive to compute (a ``GROUP BY`` over the tag rows) but is the
same for every viewer, so it is computed once and cached in Redis under a single
key, then read cheaply until the short TTL lapses -- the "periodic recompute" is
TTL-driven rather than a scheduler, matching the timeline first-page cache. When
Redis is unavailable it simply computes inline every call.
"""

import json

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.redis_client import get_redis_client
from app.repositories import hashtag_repository

_CACHE_KEY = "hashtags:trending"


def get_trending(db: Session) -> list[dict]:
    """Return the cached trending hashtags, recomputing on a miss."""
    redis_client = get_redis_client()

    cached = redis_client.get(_CACHE_KEY)
    if cached:
        return json.loads(cached)

    trending = hashtag_repository.list_trending(
        db,
        window_hours=settings.trending_window_hours,
        baseline_hours=settings.trending_baseline_hours,
        min_posts=settings.trending_min_posts,
        limit=settings.trending_limit,
    )

    redis_client.setex(
        _CACHE_KEY,
        settings.trending_cache_ttl_seconds,
        json.dumps(trending),
    )

    return trending
