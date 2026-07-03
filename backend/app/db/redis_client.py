from redis import Redis
from redis.exceptions import RedisError
from rq import Queue

from app.core.config import settings

_client: Redis | None = None
_queue: Queue | None = None


def get_redis_client() -> Redis | None:
    """
    Return a reusable Redis client.

    If Redis is unavailable, return None so the rest of the application
    can gracefully fall back to database-only behavior.
    """
    global _client

    if _client is not None:
        return _client

    try:
        _client = Redis.from_url(settings.redis_url, decode_responses=False)
        _client.ping()
    except RedisError:
        _client = None

    return _client


def get_rq_queue(queue_name: str = "default") -> Queue | None:
    """
    Return an RQ queue backed by the shared Redis connection.

    If Redis is unavailable, return None so callers can choose an inline
    fallback strategy.
    """
    global _queue

    if _queue is not None and _queue.name == queue_name:
        return _queue

    redis_client = get_redis_client()
    if redis_client is None:
        return None

    _queue = Queue(name=queue_name, connection=redis_client)
    return _queue
