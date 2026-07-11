"""
"For you" ranking.

The home timeline is chronological. "For you" is *ranked*: a post's score is its
engagement, decayed by age, lifted by the viewer's affinity for the author.

    score = (base + w_like·likes + w_rt·retweets + w_cmt·comments)   # engagement
            * 0.5 ** (age / half_life)                               # time decay
            * (1 + follow_boost·follows                              # affinity
                 + like_affinity · min(likes_on_author, cap))

Two properties make this a real ranker rather than a chronological feed with an
engagement tiebreaker:

- **Age decays engagement; it does not dominate it.** A day-old post with strong
  engagement can outrank a fresh empty one -- something a ``created_at|id`` sort
  can never express, because there the timestamp always wins and engagement only
  settles exact ties.
- **It cannot be precomputed per follower.** The score depends on *who* is
  viewing (affinity) and on *when* (decay), so fan-out on write -- which pushes
  the same chronological row into every follower's feed -- cannot produce it. A
  ranked feed is scored at read time, over a bounded candidate pool. That is
  precisely what makes the read-vs-write comparison interesting.

This module is pure: it turns numbers into a score and nothing else, so the
ranking policy can be unit-tested without a database or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class RankingWeights:
    base: float
    like: float
    retweet: float
    comment: float
    half_life_seconds: float
    follow_boost: float
    like_affinity: float
    like_affinity_cap: int


def weights_from_settings() -> RankingWeights:
    return RankingWeights(
        base=settings.ranking_base_score,
        like=settings.ranking_like_weight,
        retweet=settings.ranking_retweet_weight,
        comment=settings.ranking_comment_weight,
        half_life_seconds=settings.ranking_half_life_hours * 3600.0,
        follow_boost=settings.ranking_follow_boost,
        like_affinity=settings.ranking_like_affinity_weight,
        like_affinity_cap=settings.ranking_like_affinity_cap,
    )


def score_tweet(
    *,
    like_count: int,
    retweet_count: int,
    comment_count: int,
    age_seconds: float,
    follows_author: bool,
    viewer_likes_on_author: int,
    weights: RankingWeights,
) -> float:
    """Score one candidate post for one viewer at one moment. Higher ranks first."""
    engagement = (
        weights.base
        + weights.like * like_count
        + weights.retweet * retweet_count
        + weights.comment * comment_count
    )

    # Exponential decay: the engagement term halves every half-life. A negative
    # age (a post timestamped slightly ahead by clock skew) is clamped to "now"
    # so it cannot score *above* full strength.
    decay = 0.5 ** (max(0.0, age_seconds) / weights.half_life_seconds)

    affinity = 1.0
    if follows_author:
        affinity += weights.follow_boost
    affinity += weights.like_affinity * min(
        viewer_likes_on_author, weights.like_affinity_cap
    )

    return engagement * decay * affinity
