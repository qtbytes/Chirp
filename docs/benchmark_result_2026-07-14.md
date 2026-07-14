# Celebrity Fan-out Benchmark — Current (Unified Post) Schema

**Date:** 2026-07-14 · **Machine:** local Windows 11 dev box · **Stack:** SQLite + Redis + RQ (`SimpleWorker`)
**Script:** `backend/scripts/benchmark_celebrity_fanout.py` (rewritten for the unified `Post` model; the old script was archived in `docs/archive/benchmark_result.md`)

```sh
# from backend/, against a dedicated DB so dev data is untouched:
DATABASE_URL=sqlite:///./benchmark.db RQ_QUEUE_NAME=feed-fanout-bench \
    python -m scripts.benchmark_celebrity_fanout --followers 100000 --strategy write --delivery-mode inline
# for enqueue mode, run `python -m app.worker` with the same env first
```

## Headline numbers (100,000 followers)

| Metric | Old schema (archived) | Current schema | Notes |
|---|---|---|---|
| Posting path (create + enqueue) | ~7–12ms | **~14ms** (7ms create + 7ms enqueue) | unified Post model + hashtag/mention parsing on write |
| Fan-out job (100,001 feed rows) | 0.74s (~135K rows/s) | **0.72–0.94s (~106–139K rows/s)** | idempotent chunked insert w/ existence checks |
| Follower visibility (async, warm worker) | 0.72s | **~0.95–1.2s** | end-to-end: enqueue → RQ pickup → fan-out → visible on home timeline |
| Data prep (100K users + 100K follows) | 0.52s + 0.62s | 0.60s + 0.67s | bulk inserts, batch 10K |

The richer model (unified `Post`, visibility gating, idempotent fan-out) costs roughly
+7ms on the posting path and +0.2–0.4s on end-to-end visibility versus the archived
pre-refactor numbers. Throughput is essentially unchanged.

## Measurement pitfall found this round: Windows EcoQoS worker demotion

First runs showed visibility of **2.5–3.5s** — ~4x worse than expected. Isolation
(9 runs across process/dequeue configurations):

| Configuration | Fan-out job time |
|---|---|
| inline, in benchmark process | 0.76–0.82s |
| burst worker, in-process | 0.69s |
| burst worker, fresh cold process | 0.72s |
| non-burst worker loop, in-process, job pre-queued | 0.76s |
| `-m app.worker` subprocess, **job queued before boot** (never idles) | **0.94s** |
| `-m app.worker` subprocess, **idle on BLPOP ≥6s before job** | **2.6–3.5s** (5 runs) |

Ruled out: RQ overhead (in-process worker matches direct call), CPU throttling of
child processes (pure-CPU probe identical in parent/child), SQLite handle contention
(parent with disposed engine → still slow), poll-read contention (5s poll interval →
still slow), cold-start (2nd and 3rd consecutive jobs stay slow).

The only variable that flips the result is **whether the worker process sat blocked
on Redis BLPOP before the job arrived**. Windows 11 demotes socket-idle background
processes to efficiency mode (EcoQoS / E-cores), and the demoted worker executes the
same job ~4x slower. This is an environment artifact of idle Python workers on
Windows 11 — it does not apply to the Linux deployment, and disappears when the
worker is kept busy.

(Previous round's equivalent finding was Redis first-page-cache interference in
visibility polling; the script bypasses the cache for exactly that reason.)

## Raw run (inline, fan-out duration)

```
followers: 100,000  batch_size: 10,000  strategy: write  delivery: inline
user_create_seconds:   0.60
follow_create_seconds: 0.67
tweet_create_seconds:  0.0076
dispatch_seconds:      0.8232   <- the fan-out itself
visibility_seconds:    0.0065
delivered_rows:        100,001
throughput:            121,484 rows/s
```

## Raw run (enqueue, end-to-end, non-idled worker)

```
tweet_create_seconds: 0.0070
dispatch_seconds:     0.0069   <- posting path total ~14ms
job execution (rq):   0.94s
visibility (incl. worker boot + 200ms poll granularity): 1.21s
```

## Caveats

- Single process, single worker, local SQLite: no network hops, replication, or
  queue backlog. Postgres/MySQL + multiple workers would behave differently.
- Visibility probes one primary follower (+2 random spot-checks), polling the
  repository directly so the Redis first-page cache cannot mask staleness.
