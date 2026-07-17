"""
Prometheus metrics for the API and the fan-out pipeline.

Why these metrics and not others: the request path already fails loudly — a
broken route answers 4xx/5xx and the caller sees it. The bugs that survive
manual testing here live in the *silent* paths:

- The RQ fan-out job runs after ``POST /tweets`` has already answered 201, so a
  dead worker or a failing job is invisible from the API — followers' feeds
  just quietly stop updating. ``chirp_rq_*`` exposes queue depth, the failed-job
  registry, and worker liveness so that state shows up on a graph.
- Several Redis failures are deliberately swallowed (a dropped SSE nudge, a
  cache miss, a skipped invalidation). Best-effort without a counter is
  indistinguishable from broken; ``chirp_redis_failures_total`` is the counter.
- Sessions and the rate limiter fail *closed* with 503s, so one Redis blip
  locks everyone out. The per-route status labels on
  ``chirp_http_requests_total`` make that spike visible, and ``chirp_redis_up``
  says why.

Everything is exported from ``GET /metrics`` in the standard text format, so a
Prometheus scrape (or ``curl``) needs no extra wiring. The RQ numbers are read
from Redis at scrape time rather than cached — the scrape interval is the
staleness bound.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from redis.exceptions import RedisError
from rq.queue import Queue
from rq.worker import Worker
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.db.redis_client import get_redis_client

logger = logging.getLogger(__name__)

HTTP_REQUESTS = Counter(
    "chirp_http_requests_total",
    "HTTP requests served, by route template and status code.",
    ["method", "route", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "chirp_http_request_duration_seconds",
    "Time from request start to first response byte, by route template. "
    "First byte, not last: an SSE stream held open for an hour is one cheap "
    "response, not a one-hour outlier in the latency histogram.",
    ["method", "route"],
)

REDIS_FAILURES = Counter(
    "chirp_redis_failures_total",
    "Redis operations that failed and were degraded rather than raised: a "
    "dropped SSE nudge, a timeline cache treated as a miss, a skipped "
    "invalidation, a fan-out enqueue that ran inline instead. Each of these "
    "is deliberately survivable, which is exactly why it needs a counter — "
    "best-effort without one is indistinguishable from broken.",
    ["operation"],
)

TIMELINE_CACHE = Counter(
    "chirp_timeline_cache_total",
    "First-page timeline cache lookups, by result.",
    ["result"],
)

SSE_CONNECTIONS = Gauge(
    "chirp_sse_connections",
    "Notification SSE streams currently open.",
)

# Pre-register every label value the code can emit, so each series exists at 0
# from process start. An `increase()` alert on a series that first appears
# mid-incident fires late or not at all.
for _operation in (
    "notification_publish",
    "fanout_enqueue",
    "cache_invalidation",
    "timeline_cache_read",
    "timeline_cache_write",
):
    REDIS_FAILURES.labels(operation=_operation)
for _result in ("hit", "miss"):
    TIMELINE_CACHE.labels(result=_result)


class RQPipelineCollector:
    """
    Expose the fan-out pipeline's health, read from Redis at scrape time.

    - ``chirp_redis_up``: whether the scrape could reach Redis at all. When
      this is 0 the other families are omitted rather than reported as zero —
      an unreachable queue is unknown, not empty.
    - ``chirp_rq_queue_depth``: jobs waiting. Climbing depth with a worker
      present means the worker can't keep up; climbing with zero workers means
      nobody is draining it.
    - ``chirp_rq_failed_jobs``: the failed-job registry. Anything non-zero is a
      tweet that never reached some follower's feed and nothing else reports.
    - ``chirp_rq_workers``: workers registered against Redis. RQ expires a
      worker's registration when its heartbeat lapses, so this doubles as a
      liveness check: 0 means fan-out jobs are piling up unprocessed.
    """

    def collect(self) -> Iterator[GaugeMetricFamily]:
        up = GaugeMetricFamily(
            "chirp_redis_up",
            "Whether Redis answered this scrape (1) or not (0).",
        )
        depth = GaugeMetricFamily(
            "chirp_rq_queue_depth",
            "Jobs waiting in the RQ queue.",
            labels=["queue"],
        )
        failed = GaugeMetricFamily(
            "chirp_rq_failed_jobs",
            "Jobs in the RQ failed-job registry.",
            labels=["queue"],
        )
        workers = GaugeMetricFamily(
            "chirp_rq_workers",
            "RQ workers currently registered (heartbeat-expired workers drop out).",
        )

        try:
            connection = get_redis_client()
            queue = Queue(name=settings.rq_queue_name, connection=connection)
            depth.add_metric([queue.name], queue.count)
            failed.add_metric([queue.name], queue.failed_job_registry.count)
            workers.add_metric([], len(Worker.all(connection=connection)))
        except RedisError:
            up.add_metric([], 0)
            yield up
            return

        up.add_metric([], 1)
        yield up
        yield depth
        yield failed
        yield workers


REGISTRY.register(RQPipelineCollector())


class MetricsMiddleware:
    """
    Record one counter increment and one duration observation per request.

    Pure ASGI rather than ``BaseHTTPMiddleware`` so the SSE stream passes
    through untouched, and duration is captured at ``http.response.start`` —
    time to first byte — for the same reason.

    Routes are labelled by template (``/api/v1/tweets/{tweet_id}``), never by
    raw path: per-id paths would mint one Prometheus series per tweet.
    Requests that match no route share a single ``unmatched`` label. The
    ``/metrics`` scrape itself is not recorded — self-measurement is noise.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code: int | None = None
        duration: float | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, duration
            if message["type"] == "http.response.start":
                status_code = message["status"]
                duration = time.perf_counter() - start
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Propagating means Starlette's error middleware will answer 500;
            # record it as such so unhandled crashes are not invisible here.
            status_code = status_code or 500
            duration = duration or (time.perf_counter() - start)
            raise
        finally:
            if status_code is not None:
                # The router stamps the matched route onto the scope, so by
                # response time it is available here, outside the router.
                route = scope.get("route")
                route_label = (
                    getattr(route, "path_format", None)
                    or getattr(route, "path", None)
                    or "unmatched"
                )
                method = scope["method"]
                HTTP_REQUESTS.labels(
                    method=method,
                    route=route_label,
                    status=str(status_code),
                ).inc()
                if duration is not None:
                    HTTP_REQUEST_DURATION.labels(
                        method=method,
                        route=route_label,
                    ).observe(duration)


def metrics_response() -> Response:
    """Render the registry in the Prometheus text exposition format."""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
