from redis import Redis

from app.core.config import settings

_client: Redis | None = None


def get_redis_client() -> Redis:
    """
    Return a reusable, live Redis client.

    Redis is a hard dependency of the system, so this does not degrade to a
    None-returning stub: it pings on first use and raises ``RedisError`` if Redis
    is unreachable. The client is cached only after a successful ping, so a call
    made during an outage raises and a later call retries rather than returning a
    permanently broken connection.
    """
    global _client

    if _client is not None:
        return _client

    client = Redis.from_url(settings.redis_url, decode_responses=False)
    client.ping()
    _client = client
    return _client
