import argparse
import random
import string
import time
from dataclasses import dataclass

from sqlalchemy import delete, select

from app.db.database import Base, SessionLocal, engine
from app.models import Comment, FeedItem, Follow, Like, Tweet, User


@dataclass
class SeedStats:
    user_count: int = 0
    follow_count: int = 0
    tweet_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    feed_item_count: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed performance test data for the Twitter system.\n\n"
            "Example:\n"
            "python -m scripts.seed_performance_data "
            "--users 1000 --follows-per-user 50 --tweets-per-user 10 "
            "--likes-per-tweet 5 --comments-per-tweet 2 --fanout-write"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--users", type=int, default=1000, help="Number of users to create."
    )
    parser.add_argument(
        "--follows-per-user",
        type=int,
        default=50,
        help="Approximate number of followees per user.",
    )
    parser.add_argument(
        "--tweets-per-user",
        type=int,
        default=10,
        help="Number of tweets each user will create.",
    )
    parser.add_argument(
        "--likes-per-tweet",
        type=int,
        default=0,
        help="Number of likes to create for each tweet.",
    )
    parser.add_argument(
        "--comments-per-tweet",
        type=int,
        default=0,
        help="Number of comments to create for each tweet.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Commit batch size for inserts.",
    )
    parser.add_argument(
        "--fanout-write",
        action="store_true",
        help="Also generate feed_items so you can test fan-out on write timeline reads.",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop and recreate all tables before seeding.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible data generation.",
    )
    parser.add_argument(
        "--content-length",
        type=int,
        default=80,
        help="Approximate tweet/comment content length.",
    )
    return parser


def random_text(prefix: str, target_length: int) -> str:
    suffix_length = max(8, target_length - len(prefix) - 1)
    suffix = "".join(
        random.choices(string.ascii_lowercase + " ", k=suffix_length)
    ).strip()
    return f"{prefix} {suffix}"[:target_length].strip()


def reset_database(drop_existing: bool) -> None:
    if drop_existing:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def chunked(iterable: list[int], size: int) -> list[list[int]]:
    return [iterable[i : i + size] for i in range(0, len(iterable), size)]


def create_users(user_count: int, batch_size: int) -> tuple[list[int], SeedStats]:
    stats = SeedStats()
    user_ids: list[int] = []

    with SessionLocal() as db:
        pending: list[User] = []
        for index in range(user_count):
            pending.append(User(username=f"user_{index:06d}"))

            if len(pending) >= batch_size:
                db.add_all(pending)
                db.flush()
                user_ids.extend(user.id for user in pending)
                stats.user_count += len(pending)
                db.commit()
                pending = []

        if pending:
            db.add_all(pending)
            db.flush()
            user_ids.extend(user.id for user in pending)
            stats.user_count += len(pending)
            db.commit()

    return user_ids, stats


def create_follows(
    user_ids: list[int], follows_per_user: int, batch_size: int
) -> SeedStats:
    stats = SeedStats()
    if len(user_ids) <= 1 or follows_per_user <= 0:
        return stats

    follows_per_user = min(follows_per_user, len(user_ids) - 1)

    with SessionLocal() as db:
        pending: list[Follow] = []
        for follower_id in user_ids:
            followee_ids = random.sample(
                [uid for uid in user_ids if uid != follower_id], follows_per_user
            )

            for followee_id in followee_ids:
                pending.append(Follow(follower_id=follower_id, followee_id=followee_id))

            if len(pending) >= batch_size:
                db.add_all(pending)
                stats.follow_count += len(pending)
                db.commit()
                pending = []

        if pending:
            db.add_all(pending)
            stats.follow_count += len(pending)
            db.commit()

    return stats


def create_tweets(
    user_ids: list[int],
    tweets_per_user: int,
    batch_size: int,
    content_length: int,
) -> tuple[list[tuple[int, int]], SeedStats]:
    stats = SeedStats()
    tweet_pairs: list[tuple[int, int]] = []

    with SessionLocal() as db:
        pending: list[Tweet] = []

        for user_id in user_ids:
            for index in range(tweets_per_user):
                pending.append(
                    Tweet(
                        user_id=user_id,
                        content=random_text(
                            prefix=f"tweet_{user_id}_{index}",
                            target_length=min(280, content_length),
                        ),
                    )
                )

                if len(pending) >= batch_size:
                    db.add_all(pending)
                    db.flush()
                    tweet_pairs.extend((tweet.id, tweet.user_id) for tweet in pending)
                    stats.tweet_count += len(pending)
                    db.commit()
                    pending = []

        if pending:
            db.add_all(pending)
            db.flush()
            tweet_pairs.extend((tweet.id, tweet.user_id) for tweet in pending)
            stats.tweet_count += len(pending)
            db.commit()

    return tweet_pairs, stats


def create_likes(
    tweet_pairs: list[tuple[int, int]],
    user_ids: list[int],
    likes_per_tweet: int,
    batch_size: int,
) -> SeedStats:
    stats = SeedStats()
    if likes_per_tweet <= 0:
        return stats

    with SessionLocal() as db:
        pending: list[Like] = []

        for tweet_id, author_id in tweet_pairs:
            candidates = [uid for uid in user_ids if uid != author_id]
            if not candidates:
                continue

            current_like_count = min(likes_per_tweet, len(candidates))
            for liker_id in random.sample(candidates, current_like_count):
                pending.append(Like(user_id=liker_id, tweet_id=tweet_id))

            if len(pending) >= batch_size:
                db.add_all(pending)
                stats.like_count += len(pending)
                db.commit()
                pending = []

        if pending:
            db.add_all(pending)
            stats.like_count += len(pending)
            db.commit()

    return stats


def create_comments(
    tweet_pairs: list[tuple[int, int]],
    user_ids: list[int],
    comments_per_tweet: int,
    batch_size: int,
    content_length: int,
) -> SeedStats:
    stats = SeedStats()
    if comments_per_tweet <= 0:
        return stats

    with SessionLocal() as db:
        pending: list[Comment] = []

        for tweet_id, author_id in tweet_pairs:
            candidates = [uid for uid in user_ids if uid != author_id]
            if not candidates:
                continue

            current_comment_count = min(comments_per_tweet, len(candidates))
            commenter_ids = random.sample(candidates, current_comment_count)

            for index, commenter_id in enumerate(commenter_ids):
                pending.append(
                    Comment(
                        user_id=commenter_id,
                        tweet_id=tweet_id,
                        content=random_text(
                            prefix=f"comment_{tweet_id}_{index}",
                            target_length=max(24, min(1000, content_length)),
                        ),
                    )
                )

            if len(pending) >= batch_size:
                db.add_all(pending)
                stats.comment_count += len(pending)
                db.commit()
                pending = []

        if pending:
            db.add_all(pending)
            stats.comment_count += len(pending)
            db.commit()

    return stats


def create_feed_items(
    tweet_pairs: list[tuple[int, int]],
    batch_size: int,
) -> SeedStats:
    stats = SeedStats()

    with SessionLocal() as db:
        follow_rows = db.execute(select(Follow.follower_id, Follow.followee_id)).all()
        follower_map: dict[int, list[int]] = {}
        for follower_id, followee_id in follow_rows:
            follower_map.setdefault(followee_id, []).append(follower_id)

        tweet_rows = db.execute(select(Tweet.id, Tweet.user_id, Tweet.created_at)).all()
        pending: list[FeedItem] = []

        for tweet_id, author_id, created_at in tweet_rows:
            owner_ids = [author_id, *follower_map.get(author_id, [])]
            for owner_id in owner_ids:
                pending.append(
                    FeedItem(
                        owner_id=owner_id,
                        tweet_id=tweet_id,
                        actor_id=author_id,
                        created_at=created_at,
                    )
                )

            if len(pending) >= batch_size:
                db.add_all(pending)
                stats.feed_item_count += len(pending)
                db.commit()
                pending = []

        if pending:
            db.add_all(pending)
            stats.feed_item_count += len(pending)
            db.commit()

    return stats


def clear_existing_data_without_drop() -> None:
    with SessionLocal() as db:
        db.execute(delete(FeedItem))
        db.execute(delete(Comment))
        db.execute(delete(Like))
        db.execute(delete(Follow))
        db.execute(delete(Tweet))
        db.execute(delete(User))
        db.commit()


def print_summary(stats: SeedStats, started_at: float) -> None:
    elapsed = time.perf_counter() - started_at
    print("\nSeed complete")
    print("=" * 60)
    print(f"users:      {stats.user_count}")
    print(f"follows:    {stats.follow_count}")
    print(f"tweets:     {stats.tweet_count}")
    print(f"likes:      {stats.like_count}")
    print(f"comments:   {stats.comment_count}")
    print(f"feed_items: {stats.feed_item_count}")
    print(f"elapsed:    {elapsed:.2f}s")
    print("=" * 60)
    print("\nSuggested performance checks:")
    print("1. Start the API server.")
    print("2. Compare timeline strategies:")
    print("   GET /api/v1/timeline/home?strategy=read")
    print("   GET /api/v1/timeline/home?strategy=write")
    print("3. Test users with many follows and many tweets.")
    print("4. If using Redis, compare warm cache vs cold cache.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.users <= 0:
        raise SystemExit("--users must be greater than 0")
    if args.follows_per_user < 0:
        raise SystemExit("--follows-per-user must be >= 0")
    if args.tweets_per_user < 0:
        raise SystemExit("--tweets-per-user must be >= 0")
    if args.likes_per_tweet < 0:
        raise SystemExit("--likes-per-tweet must be >= 0")
    if args.comments_per_tweet < 0:
        raise SystemExit("--comments-per-tweet must be >= 0")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be greater than 0")

    random.seed(args.seed)
    started_at = time.perf_counter()

    print("Preparing database...")
    reset_database(drop_existing=args.drop_existing)

    if not args.drop_existing:
        print("Clearing existing rows...")
        clear_existing_data_without_drop()

    total_stats = SeedStats()

    print(f"Creating {args.users} users...")
    user_ids, stats = create_users(args.users, args.batch_size)
    total_stats.user_count += stats.user_count

    print(f"Creating follows (~{args.follows_per_user} per user)...")
    stats = create_follows(user_ids, args.follows_per_user, args.batch_size)
    total_stats.follow_count += stats.follow_count

    print(f"Creating tweets ({args.tweets_per_user} per user)...")
    tweet_pairs, stats = create_tweets(
        user_ids=user_ids,
        tweets_per_user=args.tweets_per_user,
        batch_size=args.batch_size,
        content_length=args.content_length,
    )
    total_stats.tweet_count += stats.tweet_count

    if args.likes_per_tweet > 0:
        print(f"Creating likes ({args.likes_per_tweet} per tweet)...")
        stats = create_likes(
            tweet_pairs=tweet_pairs,
            user_ids=user_ids,
            likes_per_tweet=args.likes_per_tweet,
            batch_size=args.batch_size,
        )
        total_stats.like_count += stats.like_count

    if args.comments_per_tweet > 0:
        print(f"Creating comments ({args.comments_per_tweet} per tweet)...")
        stats = create_comments(
            tweet_pairs=tweet_pairs,
            user_ids=user_ids,
            comments_per_tweet=args.comments_per_tweet,
            batch_size=args.batch_size,
            content_length=args.content_length,
        )
        total_stats.comment_count += stats.comment_count

    if args.fanout_write:
        print("Creating feed_items for fan-out on write testing...")
        stats = create_feed_items(
            tweet_pairs=tweet_pairs,
            batch_size=args.batch_size,
        )
        total_stats.feed_item_count += stats.feed_item_count

    print_summary(total_stats, started_at)


if __name__ == "__main__":
    main()
