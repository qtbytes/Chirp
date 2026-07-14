> **Archived.** These numbers were produced against the pre-refactor schema.
> The benchmark has since been rewritten for the unified `Post` model
> (`backend/scripts/benchmark_celebrity_fanout.py`); current results live in
> [`docs/benchmark_result_2026-07-14.md`](../benchmark_result_2026-07-14.md).
> Kept for historical comparison. See the root [`readme.md`](../../readme.md).

## Database Write

### 100000 followers, 10000 batch size

```sh
$ uv run benchmark-celebrity-fanout --followers 100000 --batch-size 10000 --drop-existing
Preparing database...
Clearing existing rows...
Creating celebrity and 100,000 followers...
Creating benchmark tweet...
Running fan-out benchmark...

========================================================================
Celebrity Fan-out Benchmark Result
========================================================================
followers:                  100,000
batch_size:                 10,000
fanout_strategy:            current fan-out job
user_create_seconds:        2.61
follow_create_seconds:      0.59
tweet_create_seconds:       0.0040
fanout_seconds:             4.79
delivered_rows:             100,001
throughput_rows_per_second: 20,873.47
========================================================================

Interpretation
------------------------------------------------------------------------
- Measured latency for this run: 4.79s to deliver one tweet to 100,001 timelines.
- Effective throughput: about 20,873.47 feed rows / second.
- If throughput stayed similar, 1,000,001 timeline deliveries (1 celebrity + 1e6 followers) would take about 47.91s.

Important caveat
------------------------------------------------------------------------
- This benchmark measures database-side fan-out latency in one process. It does not include queue backlog, multiple workers, network hops, replication lag, cache invalidation storms, or client-visible read delay.
- If you want a truer production-style number, run this benchmark against Postgres/MySQL instead of local SQLite, and compare one worker vs multiple workers.
$
```

### 1000000 followers, 10000 batch size

```sh
$ uv run benchmark-celebrity-fanout --followers 1000000 --batch-size 10000 --drop-existing
Preparing database...
Clearing existing rows...
Creating celebrity and 1,000,000 followers...
Creating benchmark tweet...
Running fan-out benchmark...

========================================================================
Celebrity Fan-out Benchmark Result
========================================================================
followers:                  1,000,000
batch_size:                 10,000
fanout_strategy:            current fan-out job
user_create_seconds:        27.00
follow_create_seconds:      5.74
tweet_create_seconds:       0.0038
fanout_seconds:             11.86
delivered_rows:             1,000,001
throughput_rows_per_second: 84,347.49
========================================================================

Interpretation
------------------------------------------------------------------------
- Measured latency for this run: 11.86s to deliver one tweet to 1,000,001 timelines.
- Effective throughput: about 84,347.49 feed rows / second.
- If throughput stayed similar, 1,000,001 timeline deliveries (1 celebrity + 1e6 followers) would take about 11.86s.

Important caveat
------------------------------------------------------------------------
- This benchmark measures database-side fan-out latency in one process. It does not include queue backlog, multiple workers, network hops, replication lag, cache invalidation storms, or client-visible read delay.
- If you want a truer production-style number, run this benchmark against Postgres/MySQL instead of local SQLite, and compare one worker vs multiple workers.
$
```

### 1000000 followers, 100000 batch size

```sh
$ uv run benchmark-celebrity-fanout --followers 1000000 --batch-size 100000 --drop-existing
Preparing database...
Clearing existing rows...
Creating celebrity and 1,000,000 followers...
Creating benchmark tweet...
Running fan-out benchmark...

========================================================================
Celebrity Fan-out Benchmark Result
========================================================================
followers:                  1,000,000
batch_size:                 100,000
fanout_strategy:            current fan-out job
user_create_seconds:        29.49
follow_create_seconds:      5.90
tweet_create_seconds:       0.0047
fanout_seconds:             12.14
delivered_rows:             1,000,001
throughput_rows_per_second: 82,345.22
========================================================================

Interpretation
------------------------------------------------------------------------
- Measured latency for this run: 12.14s to deliver one tweet to 1,000,001 timelines.
- Effective throughput: about 82,345.22 feed rows / second.
- If throughput stayed similar, 1,000,001 timeline deliveries (1 celebrity + 1e6 followers) would take about 12.14s.

Important caveat
------------------------------------------------------------------------
- This benchmark measures database-side fan-out latency in one process. It does not include queue backlog, multiple workers, network hops, replication lag, cache invalidation storms, or client-visible read delay.
- If you want a truer production-style number, run this benchmark against Postgres/MySQL instead of local SQLite, and compare one worker vs multiple workers.
$
```

### Optimized create user, 1000000 followers, 10000 batch size

```sh
$ uv run benchmark-celebrity-fanout --followers 1000000 --batch-size 10000 --drop-existing
Preparing database...
Clearing existing rows...
Creating celebrity and 1,000,000 followers...
Creating benchmark tweet...
Running fan-out benchmark...

========================================================================
Celebrity Fan-out Benchmark Result
========================================================================
followers:                  1,000,000
batch_size:                 10,000
fanout_strategy:            current fan-out job
user_create_seconds:        5.08
follow_create_seconds:      6.85
tweet_create_seconds:       0.0051
fanout_seconds:             11.60
delivered_rows:             1,000,001
throughput_rows_per_second: 86,184.49
========================================================================

Interpretation
------------------------------------------------------------------------
- Measured latency for this run: 11.60s to deliver one tweet to 1,000,001 timelines.
- Effective throughput: about 86,184.49 feed rows / second.
- If throughput stayed similar, 1,000,001 timeline deliveries (1 celebrity + 1e6 followers) would take about 11.60s.

Important caveat
------------------------------------------------------------------------
- This benchmark measures database-side fan-out latency in one process. It does not include queue backlog, multiple workers, network hops, replication lag, cache invalidation storms, or client-visible read delay.
- If you want a truer production-style number, run this benchmark against Postgres/MySQL instead of local SQLite, and compare one worker vs multiple workers.
$
```

## Real Time follower see a tweet from their followee

### 1e5 Followers, write strategy, inline

```

$ uv run benchmark-celebrity-fanout --followers 100000 --strategy write --delivery-mode inline --drop-existing
Preparing database...
Clearing existing rows...
Creating celebrity and 100,000 followers...
Using probes: primary=100001, random_1=83812, random_2=14594
Creating benchmark tweet...
Dispatching delivery work...
Polling primary follower 100001 timeline for visibility (strategy=write)...

========================================================================
Celebrity Timeline Visibility Benchmark Result
========================================================================
followers: 100,000
batch_size: 10,000
delivery_mode: inline
timeline_strategy: write
visibility_probe: last
user_create_seconds: 0.52
follow_create_seconds: 0.62
tweet_create_seconds: 0.0045
dispatch_seconds: 0.7408
visibility_seconds: 0.0098
delivered_rows: 100,001
throughput_rows_per_second: 134,987.14
probed_follower_ids: 100001, 83812, 14594
========================================================================

## Interpretation

- Dispatch time is how long the system spent triggering delivery work: 0.7408s.
- Visibility time is what you asked for: how long until the primary probe follower can actually see the tweet from their home timeline: 0.0098s.
- Effective feed write throughput: about 134,987.14 feed rows / second.
- If throughput stayed similar, 1,000,001 feed deliveries would take about 7.41s.

## Probe snapshots

- primary: follower_id=100001, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_1: follower_id=83812, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_2: follower_id=14594, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True

## How to read this result

- strategy=write + delivery_mode=inline: visibility time should be close to dispatch time because the work completes before polling starts.
- strategy=write + delivery_mode=enqueue: dispatch time will be small, but visibility time includes queue wait + worker execution.
- strategy=read: followers can usually see the tweet immediately after tweet creation because the timeline query reads tweets from followed authors.

## Important caveat

- This benchmark probes a primary follower plus optional random followers, not every follower timeline continuously.
- Detailed polling debug lines show whether feed rows exist for a probe follower even when the tweet is not yet visible in the top timeline page.
- For realistic queue-mode numbers, run an RQ worker in another process before using --delivery-mode enqueue.

```

### 1e6 Followers, write strategy, inline

```sh
$ uv run benchmark-celebrity-fanout --followers 1000000 --strategy write --delivery-mode inline --drop-existing
Preparing database...
Clearing existing rows...
Creating celebrity and 1,000,000 followers...
Using probes: primary=1000001, random_1=670489, random_2=116741
Creating benchmark tweet...
Dispatching delivery work...
Polling primary follower 1000001 timeline for visibility (strategy=write)...

========================================================================
Celebrity Timeline Visibility Benchmark Result
========================================================================
followers:                  1,000,000
batch_size:                 10,000
delivery_mode:              inline
timeline_strategy:          write
visibility_probe:           last
user_create_seconds:        4.98
follow_create_seconds:      6.57
tweet_create_seconds:       0.0070
dispatch_seconds:           7.5144
visibility_seconds:         0.0113
delivered_rows:             1,000,001
throughput_rows_per_second: 133,077.48
probed_follower_ids:        1000001, 670489, 116741
========================================================================

Interpretation
------------------------------------------------------------------------
- Dispatch time is how long the system spent triggering delivery work: 7.5144s.
- Visibility time is what you asked for: how long until the primary probe follower can actually see the tweet from their home timeline: 0.0113s.
- Effective feed write throughput: about 133,077.48 feed rows / second.
- If throughput stayed similar, 1,000,001 feed deliveries would take about 7.51s.

Probe snapshots
------------------------------------------------------------------------
- primary: follower_id=1000001, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_1: follower_id=670489, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_2: follower_id=116741, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True

How to read this result
------------------------------------------------------------------------
- strategy=write + delivery_mode=inline: visibility time should be close to dispatch time because the work completes before polling starts.
- strategy=write + delivery_mode=enqueue: dispatch time will be small, but visibility time includes queue wait + worker execution.
- strategy=read: followers can usually see the tweet immediately after tweet creation because the timeline query reads tweets from followed authors.

Important caveat
------------------------------------------------------------------------
- This benchmark probes a primary follower plus optional random followers, not every follower timeline continuously.
- Detailed polling debug lines show whether feed rows exist for a probe follower even when the tweet is not yet visible in the top timeline page.
- For realistic queue-mode numbers, run an RQ worker in another process before using --delivery-mode enqueue.
$
```

### 1e5 Followers, write strategy, enqueue

```sh
$ uv run benchmark-celebrity-fanout --followers 100000 --strategy write --delivery-mode enqueue --drop-existing --debug-every 5 --random-probe-count 3
Preparing database...
Clearing existing rows...
Creating celebrity and 100,000 followers...
Using probes: primary=100001, random_1=83812, random_2=14594, random_3=3280
Creating benchmark tweet...
Dispatching delivery work...
Polling primary follower 100001 timeline for visibility (strategy=write)...

========================================================================
Celebrity Timeline Visibility Benchmark Result
========================================================================
followers:                  100,000
batch_size:                 10,000
delivery_mode:              enqueue
timeline_strategy:          write
visibility_probe:           last
user_create_seconds:        0.51
follow_create_seconds:      0.63
tweet_create_seconds:       0.0048
dispatch_seconds:           0.0071
visibility_seconds:         0.7233
delivered_rows:             100,001
throughput_rows_per_second: 14,136,814.72
probed_follower_ids:        100001, 83812, 14594, 3280
========================================================================

Interpretation
------------------------------------------------------------------------
- Dispatch time is how long the system spent triggering delivery work: 0.0071s.
- Visibility time is what you asked for: how long until the primary probe follower can actually see the tweet from their home timeline: 0.7233s.
- Effective feed write throughput: about 14,136,814.72 feed rows / second.
- If throughput stayed similar, 1,000,001 feed deliveries would take about 0.07s.

Probe snapshots
------------------------------------------------------------------------
- primary: follower_id=100001, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_1: follower_id=83812, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_2: follower_id=14594, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_3: follower_id=3280, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True

How to read this result
------------------------------------------------------------------------
- strategy=write + delivery_mode=inline: visibility time should be close to dispatch time because the work completes before polling starts.
- strategy=write + delivery_mode=enqueue: dispatch time will be small, but visibility time includes queue wait + worker execution.
- strategy=read: followers can usually see the tweet immediately after tweet creation because the timeline query reads tweets from followed authors.

Important caveat
------------------------------------------------------------------------
- This benchmark probes a primary follower plus optional random followers, not every follower timeline continuously.
- Detailed polling debug lines show whether feed rows exist for a probe follower even when the tweet is not yet visible in the top timeline page.
- For realistic queue-mode numbers, run an RQ worker in another process before using --delivery-mode enqueue.
$
```

### 1e6 Followers, write strategy, enqueue

```sh
$ uv run benchmark-celebrity-fanout --followers 1000000 --strategy write --delivery-mode enqueue --drop-existing
Preparing database...
Clearing existing rows...
Creating celebrity and 1,000,000 followers...
Using probes: primary=1000001, random_1=670489, random_2=116741
Creating benchmark tweet...
Dispatching delivery work...
Polling primary follower 1000001 timeline for visibility (strategy=write)...

========================================================================
Celebrity Timeline Visibility Benchmark Result
========================================================================
followers:                  1,000,000
batch_size:                 10,000
delivery_mode:              enqueue
timeline_strategy:          write
visibility_probe:           last
user_create_seconds:        5.03
follow_create_seconds:      6.76
tweet_create_seconds:       0.0058
dispatch_seconds:           0.0069
visibility_seconds:         7.3795
delivered_rows:             1,000,001
throughput_rows_per_second: 144,544,324.52
probed_follower_ids:        1000001, 670489, 116741
========================================================================

Interpretation
------------------------------------------------------------------------
- Dispatch time is how long the system spent triggering delivery work: 0.0069s.
- Visibility time is what you asked for: how long until the primary probe follower can actually see the tweet from their home timeline: 7.3795s.
- Effective feed write throughput: about 144,544,324.52 feed rows / second.
- If throughput stayed similar, 1,000,001 feed deliveries would take about 0.01s.

Probe snapshots
------------------------------------------------------------------------
- primary: follower_id=1000001, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_1: follower_id=670489, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True
- random_2: follower_id=116741, visible=True, timeline_items=1, top_tweet_ids=[1], feed_rows_for_tweet=1, follows_celebrity=True

How to read this result
------------------------------------------------------------------------
- strategy=write + delivery_mode=inline: visibility time should be close to dispatch time because the work completes before polling starts.
- strategy=write + delivery_mode=enqueue: dispatch time will be small, but visibility time includes queue wait + worker execution.
- strategy=read: followers can usually see the tweet immediately after tweet creation because the timeline query reads tweets from followed authors.

Important caveat
------------------------------------------------------------------------
- This benchmark probes a primary follower plus optional random followers, not every follower timeline continuously.
- Detailed polling debug lines show whether feed rows exist for a probe follower even when the tweet is not yet visible in the top timeline page.
- For realistic queue-mode numbers, run an RQ worker in another process before using --delivery-mode enqueue.
```
