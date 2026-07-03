import argparse
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, func, insert, select

from app.db.database import Base, SessionLocal, engine
from app.models import FeedItem, Follow, Tweet, User
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
    probe_timeline_items: dict[str, int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark celebrity timeline visibility latency.\n\n"
            "Goal:\n"
            "Measure how long it takes from tweet creation until followers can "
            "actually see the tweet in their home timeline.\n\n"
            "Example:\n"
            "python -m scripts.benchmark_celebrity_fanout "
            "--followers 100000 --batch-size 10000 --drop-existing\n\n"
            "Notes:\n"
            "- strategy=write checks fan-out-on-write visibility via home timeline reads.\n"
            "- strategy=read checks fan-out-on-read visibility via timeline query.\n"
            "- delivery-mode=enqueue measures async dispatch time separately from visibility time.\n"
            "- Visibility polling bypasses first-page cache so it does not get stuck on a stale empty page.\n"
            "- You can probe deterministic followers (first/middle/last) or add random probes."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--followers",
        type=int,
        default=100000,
        help="Number of followers for the celebrity.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10000,
        help="Batch size for inserting users and follow rows.",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop and recreate all tables before benchmarking.",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep benchmark data instead of clearing old rows first.",
    )
    parser.add_argument(
        "--tweet-content",
        type=str,
        default="benchmark celebrity tweet",
        help="Tweet content to use for the benchmark post.",
    )
    parser.add_argument(
        "--use-direct-insert",
        action="store_true",
        help=(
            "Use a direct bulk SQL fan-out path instead of the current "
            "application fan-out job. Only meaningful with --strategy write."
        ),
    )
    parser.add_argument(
        "--strategy",
        choices=("write", "read"),
        default="write",
        help=(
            "Timeline read strategy to validate follower visibility. "
            "'write' checks feed_items-backed home timeline. "
            "'read' checks home timeline built from follows+tweets."
        ),
    )
    parser.add_argument(
        "--delivery-mode",
        choices=("inline", "enqueue"),
        default="inline",
        help=(
            "How to trigger fan-out work for write strategy. "
            "'inline' runs in the current process. "
            "'enqueue' enqueues the job and polls for visibility."
        ),
    )
    parser.add_argument(
        "--visibility-probe",
        choices=("first", "middle", "last", "random"),
        default="last",
        help=(
            "Which primary follower to use when checking visibility. "
            "'random' picks one follower randomly."
        ),
    )
    parser.add_argument(
        "--random-probe-count",
        type=int,
        default=2,
        help=(
            "Additional random followers to probe and print during polling. "
            "Set to 0 to disable extra random probes."
        ),
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed used for choosing random follower probes.",
    )
    parser.add_argument(
        "--timeline-limit",
        type=int,
        default=20,
        help="Timeline page size used when probing visibility.",
    )
    parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=100,
        help="Polling interval in milliseconds while waiting for visibility.",
    )
    parser.add_argument(
        "--visibility-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum time to wait for follower timeline visibility.",
    )
    parser.add_argument(
        "--debug-every",
        type=int,
        default=10,
        help=(
            "Print detailed polling debug output every N polling attempts. "
            "Also prints immediately when visibility is detected."
        ),
    )
    return parser


def reset_database(drop_existing: bool) -> None:
    if drop_existing:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def clear_existing_data() -> None:
    with SessionLocal() as db:
        db.execute(delete(FeedItem))
        db.execute(delete(Follow))
        db.execute(delete(Tweet))
        db.execute(delete(User))
        db.commit()


def create_celebrity_and_followers(
    follower_count: int,
    batch_size: int,
) -> tuple[int, int, float, float]:
    started_users = time.perf_counter()

    with SessionLocal() as db:
        db.execute(insert(User), [{"username": "celebrity_benchmark"}])
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
                {"username": f"fan_{created_followers + offset:07d}"}
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
    if not follower_ids:
        raise RuntimeError("no benchmark followers found")

    probe_map: dict[str, int] = {}
    primary_probe_id = choose_primary_probe_follower_id(
        follower_ids,
        primary_probe_mode,
        rng,
    )
    probe_map["primary"] = primary_probe_id

    candidates = [
        follower_id for follower_id in follower_ids if follower_id != primary_probe_id
    ]
    sample_count = min(max(0, random_probe_count), len(candidates))
    if sample_count > 0:
        for index, follower_id in enumerate(
            rng.sample(candidates, sample_count), start=1
        ):
            probe_map[f"random_{index}"] = follower_id

    return probe_map


def create_benchmark_tweet(author_id: int, content: str) -> tuple[int, datetime, float]:
    started = time.perf_counter()

    with SessionLocal() as db:
        tweet = Tweet(user_id=author_id, content=content)
        db.add(tweet)
        db.commit()
        db.refresh(tweet)

        tweet_id = tweet.id
        created_at = tweet.created_at

    return tweet_id, created_at, time.perf_counter() - started


def count_delivered_rows(tweet_id: int) -> int:
    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(FeedItem)
                .where(FeedItem.tweet_id == tweet_id)
            )
            or 0
        )


def count_feed_rows_for_owner(owner_id: int, tweet_id: int) -> int:
    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(FeedItem)
                .where(
                    FeedItem.owner_id == owner_id,
                    FeedItem.tweet_id == tweet_id,
                )
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
    *,
    tweet_id: int,
    author_id: int,
    created_at: datetime,
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
                "tweet_id": tweet_id,
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
            tweet_id=tweet_id,
            author_id=author_id,
            created_at=created_at,
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
        service = TimelineService(db)

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
            rows = tweet_repository.list_tweets_by_authors(
                db,
                author_ids=author_ids,
                limit=limit,
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
        follower_id=follower_id,
        strategy=strategy,
        limit=limit,
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
    debug_every: int,
) -> tuple[float, TimelinePage, dict[str, ProbeSnapshot]]:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    attempts = 0
    latest_snapshots: dict[str, ProbeSnapshot] = {}
    debug_every = max(1, debug_every)

    while time.perf_counter() <= deadline:
        attempts += 1

        for probe_name, follower_id in probe_map.items():
            latest_snapshots[probe_name] = collect_probe_snapshot(
                probe_name=probe_name,
                follower_id=follower_id,
                tweet_id=tweet_id,
                celebrity_id=celebrity_id,
                strategy=strategy,
                limit=limit,
            )

        primary_snapshot = latest_snapshots["primary"]
        elapsed = time.perf_counter() - started

        # should_print_debug = (
        #     attempts == 1 or attempts % debug_every == 0 or primary_snapshot.visible
        # )
        # if should_print_debug:
        #     delivered_rows = count_delivered_rows(tweet_id)
        #     print(
        #         f"[poll-debug] attempt={attempts} "
        #         f"elapsed={elapsed:.2f}s "
        #         f"strategy={strategy} "
        #         f"tweet_id={tweet_id} "
        #         f"total_delivered_rows={delivered_rows}"
        #     )
        #     for probe_name in probe_map:
        #         print(
        #             f"[poll-debug] {format_probe_snapshot(latest_snapshots[probe_name])}"
        #         )

        if primary_snapshot.visible:
            page = load_uncached_timeline_page(
                follower_id=primary_snapshot.follower_id,
                strategy=strategy,
                limit=limit,
            )
            return elapsed, page, latest_snapshots

        time.sleep(poll_interval_ms / 1000)

    delivered_rows = count_delivered_rows(tweet_id)
    debug_lines = [
        "tweet did not become visible in follower timeline before timeout",
        f"strategy={strategy}",
        f"tweet_id={tweet_id}",
        f"celebrity_id={celebrity_id}",
        f"delivered_rows={delivered_rows}",
        f"poll_attempts={attempts}",
    ]
    for probe_name in probe_map:
        snapshot = latest_snapshots.get(probe_name)
        if snapshot is not None:
            debug_lines.append(format_probe_snapshot(snapshot))
    raise TimeoutError(" | ".join(debug_lines))


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
    print("Celebrity Timeline Visibility Benchmark Result")
    print("=" * 72)
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
        f"{', '.join(str(follower_id) for follower_id in result.probed_follower_ids)}"
    )
    print("=" * 72)
    print()

    estimated_for_1m = (
        1_000_001 / result.throughput_rows_per_second
        if result.throughput_rows_per_second > 0
        else math.inf
    )

    print("Interpretation")
    print("-" * 72)
    print(
        f"- Dispatch time is how long the system spent triggering delivery work: "
        f"{result.dispatch_seconds:.4f}s."
    )
    print(
        f"- Visibility time is what you asked for: how long until the primary "
        f"probe follower can actually see the tweet from their home timeline: "
        f"{result.visibility_seconds:.4f}s."
    )
    print(
        f"- Effective feed write throughput: about "
        f"{format_number(result.throughput_rows_per_second)} feed rows / second."
    )
    print(
        f"- If throughput stayed similar, 1,000,001 feed deliveries would take "
        f"about {estimated_for_1m:.2f}s."
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
        "- strategy=write + delivery_mode=inline: visibility time should be close "
        "to dispatch time because the work completes before polling starts."
    )
    print(
        "- strategy=write + delivery_mode=enqueue: dispatch time will be small, "
        "but visibility time includes queue wait + worker execution."
    )
    print(
        "- strategy=read: followers can usually see the tweet immediately after "
        "tweet creation because the timeline query reads tweets from followed authors."
    )
    print()
    print("Important caveat")
    print("-" * 72)
    print(
        "- This benchmark probes a primary follower plus optional random followers, "
        "not every follower timeline continuously."
    )
    print(
        "- Detailed polling debug lines show whether feed rows exist for a probe "
        "follower even when the tweet is not yet visible in the top timeline page."
    )
    print(
        "- For realistic queue-mode numbers, run an RQ worker in another process "
        "before using --delivery-mode enqueue."
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.followers <= 0:
        raise SystemExit("--followers must be greater than 0")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be greater than 0")
    if args.timeline_limit <= 0:
        raise SystemExit("--timeline-limit must be greater than 0")
    if args.poll_interval_ms <= 0:
        raise SystemExit("--poll-interval-ms must be greater than 0")
    if args.visibility_timeout_seconds <= 0:
        raise SystemExit("--visibility-timeout-seconds must be greater than 0")
    if args.random_probe_count < 0:
        raise SystemExit("--random-probe-count must be >= 0")
    if args.debug_every <= 0:
        raise SystemExit("--debug-every must be greater than 0")
    if args.delivery_mode == "enqueue" and args.use_direct_insert:
        raise SystemExit(
            "--use-direct-insert cannot be combined with --delivery-mode enqueue"
        )

    rng = random.Random(args.random_seed)

    print("Preparing database...")
    reset_database(drop_existing=args.drop_existing)

    if not args.keep_data:
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
        + ", ".join(
            f"{probe_name}={follower_id}"
            for probe_name, follower_id in probe_map.items()
        )
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
    visibility_seconds, page, probe_snapshots = wait_for_visibility(
        probe_map=probe_map,
        tweet_id=tweet_id,
        celebrity_id=celebrity_id,
        strategy=args.strategy,
        limit=args.timeline_limit,
        timeout_seconds=args.visibility_timeout_seconds,
        poll_interval_ms=args.poll_interval_ms,
        debug_every=args.debug_every,
    )

    delivered_rows = count_delivered_rows(tweet_id)
    throughput_rows_per_second = (
        delivered_rows / dispatch_seconds if dispatch_seconds > 0 else 0.0
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
        probed_follower_ids=[probe_map[probe_name] for probe_name in probe_map],
        probe_timeline_items={
            probe_name: snapshot.timeline_items
            for probe_name, snapshot in probe_snapshots.items()
        },
    )

    print_result(result, probe_snapshots)


if __name__ == "__main__":
    main()
