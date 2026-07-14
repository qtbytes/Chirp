"""
Celebrity fan-out / timeline-visibility benchmark, current (unified Post) schema.

Measures, for a celebrity with N followers:
- tweet_create_seconds: the posting path (tweet_repository.create_tweet, the
  same code the API route runs),
- dispatch_seconds:     how long triggering fan-out takes (inline job run, or
  just enqueueing to RQ),
- visibility_seconds:   how long until a probe follower actually sees the
  tweet on their home timeline (polls the repository directly, bypassing the
  Redis first-page cache so measurements aren't contaminated by caching).

Safety: refuses to run unless the configured database URL contains
"benchmark", because it wipes every table. Run it like:

    DATABASE_URL=sqlite:///./benchmark.db RQ_QUEUE_NAME=feed-fanout-bench \
        python -m scripts.benchmark_celebrity_fanout --followers 100000

For --delivery-mode enqueue, start a worker with the SAME env first:

    DATABASE_URL=sqlite:///./benchmark.db RQ_QUEUE_NAME=feed-fanout-bench \
        python -m app.worker
"""

import argparse
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, func, insert, select

from app.core.config import settings
from app.db.database import Base, SessionLocal, engine
from app.models import (
    Block,
    FeedItem,
    Follow,
    Like,
    Mute,
    Notification,
    Post,
    PostHashtag,
    PostMention,
    PostView,
    Report,
    User,
)
from app.repositories import feed_repository, follow_repository, tweet_repository
from app.schemas.tweet import TimelinePage
from app.services.timeline_service import (
    TimelineService,
    enqueue_feed_fanout_job,
    run_feed_fanout_job,
)

ProbeMode = Literal["first", "middle", "last", "random"]
TimelineStrategy = Literal["write", "read"]
DeliveryMode = Literal["inline", "enqueue"]

BENCHMARK_PASSWORD_HASH = "benchmark-not-a-real-hash"


@dataclass
class ProbeSnapshot:
    probe_name: str
    follower_id: int
    visible: bool
    timeline_items: int
    top_tweet_ids: list[int]
    delivered_feed_rows_for_follower: int
    follows_celebrity: bool


@dataclass
class BenchmarkResult:
    follower_count: int
    batch_size: int
    delivery_mode: str
    timeline_strategy: str
    visibility_probe: str
    user_create_seconds: float
    follow_create_seconds: float
    tweet_create_seconds: float
    dispatch_seconds: float
    visibility_seconds: float
    delivered_rows: int
    throughput_rows_per_second: float
    probed_follower_ids: list[int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark celebrity timeline visibility latency on the current "
            "unified Post schema.\n\n"
            "Example:\n"
            "python -m scripts.benchmark_celebrity_fanout "
            "--followers 100000 --strategy write --delivery-mode inline\n\n"
            "Notes:\n"
            "- strategy=write checks fan-out-on-write visibility via feed_items.\n"
            "- strategy=read checks fan-out-on-read visibility via the follows query.\n"
            "- delivery-mode=enqueue needs an RQ worker running with the same "
            "DATABASE_URL and RQ_QUEUE_NAME.\n"
            "- Visibility polling bypasses the Redis first-page cache."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--followers", type=int, default=100000)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Reuse existing benchmark users/follows instead of recreating them.",
    )
    parser.add_argument(
        "--tweet-content", type=str, default="benchmark celebrity tweet"
    )
    parser.add_argument(
        "--use-direct-insert",
        action="store_true",
        help="Bypass the app fan-out job and bulk-insert feed rows directly.",
    )
    parser.add_argument("--strategy", choices=("write", "read"), default="write")
    parser.add_argument(
        "--delivery-mode", choices=("inline", "enqueue"), default="inline"
    )
    parser.add_argument(
        "--visibility-probe",
        choices=("first", "middle", "last", "random"),
        default="last",
    )
    parser.add_argument("--random-probe-count", type=int, default=2)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--timeline-limit", type=int, default=20)
    parser.add_argument("--poll-interval-ms", type=int, default=50)
    parser.add_argument("--visibility-timeout-seconds", type=float, default=120.0)
    return parser


def ensure_benchmark_database() -> None:
    """Refuse to wipe anything that is not explicitly a benchmark database."""
    if "benchmark" not in settings.database_url:
        raise SystemExit(
            "Refusing to run: DATABASE_URL must point at a dedicated benchmark "
            "database (a URL containing 'benchmark'), e.g.\n"
            "  DATABASE_URL=sqlite:///./benchmark.db\n"
            f"Current: {settings.database_url}"
        )


def reset_database() -> None:
    Base.metadata.create_all(bind=engine)


def clear_existing_data() -> None:
    with SessionLocal() as db:
        for model in (
            FeedItem,
            PostView,
            PostHashtag,
            PostMention,
            Like,
            Notification,
            Report,
            Block,
            Mute,
            Follow,
            Post,
            User,
        ):
            db.execute(delete(model))
        db.commit()


def create_celebrity_and_followers(
    follower_count: int,
    batch_size: int,
) -> tuple[int, int, float, float]:
    started_users = time.perf_counter()

    with SessionLocal() as db:
        db.execute(
            insert(User),
            [
                {
                    "username": "celebrity_benchmark",
                    "password_hash": BENCHMARK_PASSWORD_HASH,
                }
            ],
        )
        db.commit()

        celebrity_id = db.scalar(
            select(User.id).where(User.username == "celebrity_benchmark")
        )
        if celebrity_id is None:
            raise RuntimeError("failed to create celebrity benchmark user")

        created_followers = 0
        while created_followers < follower_count:
            current_batch_size = min(batch_size, follower_count - created_followers)
            payload = [
                {
                    "username": f"fan_{created_followers + offset:07d}",
                    "password_hash": BENCHMARK_PASSWORD_HASH,
                }
                for offset in range(current_batch_size)
            ]
            db.execute(insert(User), payload)
            db.commit()
            created_followers += current_batch_size

    user_create_seconds = time.perf_counter() - started_users

    started_follows = time.perf_counter()

    with SessionLocal() as db:
        celebrity_id = db.scalar(
            select(User.id).where(User.username == "celebrity_benchmark")
        )
        if celebrity_id is None:
            raise RuntimeError("failed to load celebrity benchmark user")

        follower_ids = (
            db.execute(
                select(User.id).where(User.username.like("fan_%")).order_by(User.id)
            )
            .scalars()
            .all()
        )

        created_follows = 0
        while created_follows < len(follower_ids):
            current_batch_ids = follower_ids[
                created_follows : created_follows + batch_size
            ]
            payload = [
                {"follower_id": follower_id, "followee_id": celebrity_id}
                for follower_id in current_batch_ids
            ]
            db.execute(insert(Follow), payload)
            db.commit()
            created_follows += len(current_batch_ids)

    follow_create_seconds = time.perf_counter() - started_follows
    return celebrity_id, len(follower_ids), user_create_seconds, follow_create_seconds


def load_celebrity_id() -> int:
    with SessionLocal() as db:
        celebrity_id = db.scalar(
            select(User.id).where(User.username == "celebrity_benchmark")
        )
    if celebrity_id is None:
        raise RuntimeError("no benchmark celebrity found; run without --keep-data")
    return celebrity_id


def load_all_follower_ids() -> list[int]:
    with SessionLocal() as db:
        return (
            db.execute(
                select(User.id).where(User.username.like("fan_%")).order_by(User.id)
            )
            .scalars()
            .all()
        )


def choose_primary_probe_follower_id(
    follower_ids: list[int],
    position: ProbeMode,
    rng: random.Random,
) -> int:
    if not follower_ids:
        raise RuntimeError("no benchmark followers found")

    if position == "first":
        return follower_ids[0]
    if position == "middle":
        return follower_ids[len(follower_ids) // 2]
    if position == "last":
        return follower_ids[-1]
    return rng.choice(follower_ids)


def build_probe_map(
    *,
    follower_ids: list[int],
    primary_probe_mode: ProbeMode,
    random_probe_count: int,
    rng: random.Random,
) -> dict[str, int]:
    probe_map: dict[str, int] = {}
    primary_probe_id = choose_primary_probe_follower_id(
        follower_ids, primary_probe_mode, rng
    )
    probe_map["primary"] = primary_probe_id

    candidates = [fid for fid in follower_ids if fid != primary_probe_id]
    sample_count = min(max(0, random_probe_count), len(candidates))
    for index, follower_id in enumerate(rng.sample(candidates, sample_count), start=1):
        probe_map[f"random_{index}"] = follower_id

    return probe_map


def create_benchmark_tweet(author_id: int, content: str) -> tuple[int, datetime, float]:
    """The real posting path: the same repository call the API route makes."""
    started = time.perf_counter()

    with SessionLocal() as db:
        post = tweet_repository.create_tweet(db, author_id=author_id, content=content)
        if post is None:
            raise RuntimeError("failed to create benchmark tweet")
        tweet_id = post.id
        created_at = post.created_at

    return tweet_id, created_at, time.perf_counter() - started


def count_delivered_rows(tweet_id: int) -> int:
    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(FeedItem)
                .where(FeedItem.post_id == tweet_id)
            )
            or 0
        )


def count_feed_rows_for_owner(owner_id: int, tweet_id: int) -> int:
    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(FeedItem)
                .where(FeedItem.owner_id == owner_id, FeedItem.post_id == tweet_id)
            )
            or 0
        )


def follower_follows_celebrity(follower_id: int, celebrity_id: int) -> bool:
    with SessionLocal() as db:
        row = db.scalar(
            select(func.count())
            .select_from(Follow)
            .where(
                Follow.follower_id == follower_id,
                Follow.followee_id == celebrity_id,
            )
        )
    return bool(row)


def run_direct_bulk_fanout(
    *, tweet_id: int, author_id: int, created_at: datetime
) -> None:
    with SessionLocal() as db:
        follower_ids = (
            db.execute(
                select(Follow.follower_id).where(Follow.followee_id == author_id)
            )
            .scalars()
            .all()
        )
        owner_ids = [author_id, *follower_ids]
        payload = [
            {
                "owner_id": owner_id,
                "post_id": tweet_id,
                "actor_id": author_id,
                "created_at": created_at,
            }
            for owner_id in owner_ids
        ]
        db.execute(insert(FeedItem), payload)
        db.commit()


def dispatch_delivery(
    *,
    tweet_id: int,
    author_id: int,
    created_at: datetime,
    strategy: TimelineStrategy,
    delivery_mode: DeliveryMode,
    use_direct_insert: bool,
) -> float:
    started = time.perf_counter()

    if strategy == "read":
        return time.perf_counter() - started

    if use_direct_insert:
        run_direct_bulk_fanout(
            tweet_id=tweet_id, author_id=author_id, created_at=created_at
        )
    elif delivery_mode == "enqueue":
        enqueue_feed_fanout_job(tweet_id=tweet_id, author_id=author_id)
    else:
        run_feed_fanout_job(tweet_id=tweet_id, author_id=author_id)

    return time.perf_counter() - started


def load_uncached_timeline_page(
    *,
    follower_id: int,
    strategy: TimelineStrategy,
    limit: int,
) -> TimelinePage:
    with SessionLocal() as db:
        service = TimelineService(db, viewer_id=follower_id)

        if strategy == "write":
            rows = feed_repository.list_feed_tweets(
                db,
                owner_id=follower_id,
                limit=limit,
            )
        else:
            followee_ids = follow_repository.list_followee_ids(
                db,
                follower_id=follower_id,
            )
            author_ids = [follower_id, *followee_ids]
            rows = tweet_repository.list_feed_with_retweets(
                db,
                author_ids=author_ids,
                limit=limit,
                current_user_id=follower_id,
            )

        return service._build_page(rows=rows, limit=limit, strategy=strategy)


def collect_probe_snapshot(
    *,
    probe_name: str,
    follower_id: int,
    tweet_id: int,
    celebrity_id: int,
    strategy: TimelineStrategy,
    limit: int,
) -> ProbeSnapshot:
    page = load_uncached_timeline_page(
        follower_id=follower_id, strategy=strategy, limit=limit
    )
    visible = any(item.id == tweet_id for item in page.items)
    top_tweet_ids = [item.id for item in page.items[:5]]

    return ProbeSnapshot(
        probe_name=probe_name,
        follower_id=follower_id,
        visible=visible,
        timeline_items=len(page.items),
        top_tweet_ids=top_tweet_ids,
        delivered_feed_rows_for_follower=count_feed_rows_for_owner(
            follower_id, tweet_id
        ),
        follows_celebrity=follower_follows_celebrity(follower_id, celebrity_id),
    )


def format_probe_snapshot(snapshot: ProbeSnapshot) -> str:
    top_ids = ", ".join(str(tweet_id) for tweet_id in snapshot.top_tweet_ids) or "-"
    return (
        f"{snapshot.probe_name}: "
        f"follower_id={snapshot.follower_id}, "
        f"visible={snapshot.visible}, "
        f"timeline_items={snapshot.timeline_items}, "
        f"top_tweet_ids=[{top_ids}], "
        f"feed_rows_for_tweet={snapshot.delivered_feed_rows_for_follower}, "
        f"follows_celebrity={snapshot.follows_celebrity}"
    )


def wait_for_visibility(
    *,
    probe_map: dict[str, int],
    tweet_id: int,
    celebrity_id: int,
    strategy: TimelineStrategy,
    limit: int,
    timeout_seconds: float,
    poll_interval_ms: int,
) -> tuple[float, dict[str, ProbeSnapshot]]:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    latest_snapshots: dict[str, ProbeSnapshot] = {}

    while time.perf_counter() <= deadline:
        # Probe the primary follower first and time-stamp on ITS visibility;
        # secondary probes are informational.
        primary_page = load_uncached_timeline_page(
            follower_id=probe_map["primary"], strategy=strategy, limit=limit
        )
        elapsed = time.perf_counter() - started
        if any(item.id == tweet_id for item in primary_page.items):
            for probe_name, follower_id in probe_map.items():
                latest_snapshots[probe_name] = collect_probe_snapshot(
                    probe_name=probe_name,
                    follower_id=follower_id,
                    tweet_id=tweet_id,
                    celebrity_id=celebrity_id,
                    strategy=strategy,
                    limit=limit,
                )
            return elapsed, latest_snapshots

        time.sleep(poll_interval_ms / 1000)

    delivered_rows = count_delivered_rows(tweet_id)
    raise TimeoutError(
        "tweet did not become visible before timeout | "
        f"strategy={strategy} tweet_id={tweet_id} celebrity_id={celebrity_id} "
        f"delivered_rows={delivered_rows}"
    )


def format_number(value: int | float) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.2f}"


def print_result(
    result: BenchmarkResult,
    probe_snapshots: dict[str, ProbeSnapshot],
) -> None:
    print()
    print("=" * 72)
    print("Celebrity Timeline Visibility Benchmark Result (unified Post schema)")
    print("=" * 72)
    print(f"database:                   {settings.database_url}")
    print(f"queue:                      {settings.rq_queue_name}")
    print(f"followers:                  {format_number(result.follower_count)}")
    print(f"batch_size:                 {format_number(result.batch_size)}")
    print(f"delivery_mode:              {result.delivery_mode}")
    print(f"timeline_strategy:          {result.timeline_strategy}")
    print(f"visibility_probe:           {result.visibility_probe}")
    print(f"user_create_seconds:        {result.user_create_seconds:.2f}")
    print(f"follow_create_seconds:      {result.follow_create_seconds:.2f}")
    print(f"tweet_create_seconds:       {result.tweet_create_seconds:.4f}")
    print(f"dispatch_seconds:           {result.dispatch_seconds:.4f}")
    print(f"visibility_seconds:         {result.visibility_seconds:.4f}")
    print(f"delivered_rows:             {format_number(result.delivered_rows)}")
    print(
        f"throughput_rows_per_second: "
        f"{format_number(result.throughput_rows_per_second)}"
    )
    print(
        f"probed_follower_ids:        "
        f"{', '.join(str(fid) for fid in result.probed_follower_ids)}"
    )
    print("=" * 72)
    print()

    posting_path_ms = (result.tweet_create_seconds + result.dispatch_seconds) * 1000
    estimated_for_1m = (
        1_000_001 / result.throughput_rows_per_second
        if result.throughput_rows_per_second > 0
        else math.inf
    )

    print("Interpretation")
    print("-" * 72)
    print(
        f"- Posting path (create tweet + dispatch fan-out): {posting_path_ms:.1f}ms."
    )
    print(
        f"- Visibility: primary probe follower sees the tweet on their home "
        f"timeline after {result.visibility_seconds:.4f}s."
    )
    print(
        f"- Effective feed write throughput: about "
        f"{format_number(result.throughput_rows_per_second)} feed rows / second."
    )
    print(
        f"- At that throughput, 1,000,001 deliveries would take about "
        f"{estimated_for_1m:.2f}s."
    )
    print()

    print("Probe snapshots")
    print("-" * 72)
    for probe_name in probe_snapshots:
        print(f"- {format_probe_snapshot(probe_snapshots[probe_name])}")
    print()

    print("How to read this result")
    print("-" * 72)
    print(
        "- write + inline: visibility ~= 0 because fan-out completed before "
        "polling; dispatch_seconds IS the fan-out duration."
    )
    print(
        "- write + enqueue: dispatch is just the enqueue; visibility includes "
        "queue wait + worker fan-out execution."
    )
    print("- read: no fan-out; the timeline query finds the tweet immediately.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.followers <= 0:
        raise SystemExit("--followers must be greater than 0")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be greater than 0")
    if args.delivery_mode == "enqueue" and args.use_direct_insert:
        raise SystemExit(
            "--use-direct-insert cannot be combined with --delivery-mode enqueue"
        )

    ensure_benchmark_database()
    rng = random.Random(args.random_seed)

    print(f"Preparing database ({settings.database_url})...")
    reset_database()

    if args.keep_data:
        celebrity_id = load_celebrity_id()
        user_create_seconds = 0.0
        follow_create_seconds = 0.0
        # Remove earlier benchmark tweets/feed rows so each run measures one
        # fresh tweet against clean timelines.
        with SessionLocal() as db:
            db.execute(delete(FeedItem))
            db.execute(delete(PostView))
            db.execute(delete(PostHashtag))
            db.execute(delete(PostMention))
            db.execute(delete(Post))
            db.commit()
        follower_count = len(load_all_follower_ids())
    else:
        print("Clearing existing rows...")
        clear_existing_data()
        print(f"Creating celebrity and {args.followers:,} followers...")
        celebrity_id, follower_count, user_create_seconds, follow_create_seconds = (
            create_celebrity_and_followers(
                follower_count=args.followers,
                batch_size=args.batch_size,
            )
        )

    follower_ids = load_all_follower_ids()
    probe_map = build_probe_map(
        follower_ids=follower_ids,
        primary_probe_mode=args.visibility_probe,
        random_probe_count=args.random_probe_count,
        rng=rng,
    )
    print(
        "Using probes: "
        + ", ".join(f"{name}={fid}" for name, fid in probe_map.items())
    )

    print("Creating benchmark tweet...")
    tweet_id, created_at, tweet_create_seconds = create_benchmark_tweet(
        author_id=celebrity_id,
        content=args.tweet_content,
    )

    print("Dispatching delivery work...")
    dispatch_seconds = dispatch_delivery(
        tweet_id=tweet_id,
        author_id=celebrity_id,
        created_at=created_at,
        strategy=args.strategy,
        delivery_mode=args.delivery_mode,
        use_direct_insert=args.use_direct_insert,
    )

    print(
        f"Polling primary follower {probe_map['primary']} timeline for visibility "
        f"(strategy={args.strategy})..."
    )
    visibility_seconds, probe_snapshots = wait_for_visibility(
        probe_map=probe_map,
        tweet_id=tweet_id,
        celebrity_id=celebrity_id,
        strategy=args.strategy,
        limit=args.timeline_limit,
        timeout_seconds=args.visibility_timeout_seconds,
        poll_interval_ms=args.poll_interval_ms,
    )

    delivered_rows = count_delivered_rows(tweet_id)
    fanout_seconds = (
        dispatch_seconds if args.delivery_mode == "inline" else visibility_seconds
    )
    throughput_rows_per_second = (
        delivered_rows / fanout_seconds if fanout_seconds > 0 else 0.0
    )

    result = BenchmarkResult(
        follower_count=follower_count,
        batch_size=args.batch_size,
        delivery_mode=args.delivery_mode,
        timeline_strategy=args.strategy,
        visibility_probe=args.visibility_probe,
        user_create_seconds=user_create_seconds,
        follow_create_seconds=follow_create_seconds,
        tweet_create_seconds=tweet_create_seconds,
        dispatch_seconds=dispatch_seconds,
        visibility_seconds=visibility_seconds,
        delivered_rows=delivered_rows,
        throughput_rows_per_second=throughput_rows_per_second,
        probed_follower_ids=[probe_map[name] for name in probe_map],
    )

    print_result(result, probe_snapshots)


if __name__ == "__main__":
    main()
