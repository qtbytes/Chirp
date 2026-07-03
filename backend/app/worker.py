from redis.exceptions import RedisError

# from rq import Worker # don't works on windows
from rq import SimpleWorker as Worker

from app.core.config import settings
from app.db.redis_client import get_redis_client


def main() -> None:
    """
    Start an RQ worker for the configured queue.

    Usage:
        uv run python -m app.worker
    """
    redis = get_redis_client()
    if redis is None:
        raise RuntimeError(
            f"Unable to connect to Redis at {settings.redis_url}. "
            "RQ worker cannot start without Redis."
        )

    queue_names = [settings.rq_queue_name]

    try:
        worker = Worker(queue_names, connection=redis)
        worker.work()
    except RedisError as exc:
        raise RuntimeError(
            "RQ worker stopped because Redis became unavailable."
        ) from exc


if __name__ == "__main__":
    main()
