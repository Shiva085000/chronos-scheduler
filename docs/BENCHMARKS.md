# Benchmarks

Measured on the docker-compose stack (single host, Postgres 16, Redis 7),
2026-07-04. Method: `app/scripts/bench.py` bulk-inserts N `demo.echo` jobs
(no handler work — the numbers measure the *scheduler*: claim, dispatch,
finalize round-trips), waits for the fleet to drain them, and computes stats
from the database's own timestamps. Each run cleans up after itself.
Reproduce with:

```bash
docker compose up -d --scale worker=N worker
docker compose exec api python -m app.scripts.bench --jobs 2000
```

## Throughput scaling (2,000 jobs; 4,000 at 8 workers)

| Workers (×4 concurrency) | Throughput | Scaling vs 1 worker | exec+finalize p95 |
|---|---|---|---|
| 1 | 309 jobs/s | 1.00× | 7 ms |
| 2 | 614 jobs/s | 1.99× | 7 ms |
| 4 | 1,014 jobs/s | 3.28× | 10 ms |
| 8 | 1,505 jobs/s | 4.87× | 13 ms |

**Throughput scales linearly to 2 workers and sub-linearly beyond, with
database contention becoming dominant around ~1,000–1,500 claims/sec on this
host** — consistent with the design doc's predicted "low thousands/sec"
ceiling for a single-Postgres queue. The rising finalize p95 (7 → 13 ms) is
the contention signature: more claimers means more lock traffic on the queue
head and more round-trip queuing in Postgres. Past this point the design
calls for per-queue sharding, then partitioning, then a broker with an
outbox (see ARCHITECTURE.md, scaling path).

## Latency

| Measurement | Value | Condition |
|---|---|---|
| Dispatch latency (enqueue → claimed), p50 / p95 | **35 ms / 54 ms** | idle fleet, 50 jobs into 64 free slots — no queueing; this is the Redis-wake fast path |
| Claim wait under saturation, p50 | 1.1–3.3 s | 2,000-job burst against 8–64 slots; queue depth, not dispatch cost (Little's law) |
| Execution + finalize, p50 | 6–10 ms | echo handler; two guarded UPDATEs + attempt close |
| Retry latency (failure → next attempt running) | backoff + ~1 s | measured on the timeout demo: attempt 1 → attempt 2 in 10.0 s with a 5 s timeout + ~3.6 s jittered backoff |
| Crash recovery (SIGKILL → attempt marked `lost`) | ~35 s | lease 30 s + reaper sweep ≤ 5 s; measured in live fault injection |
| Graceful shutdown, idle worker | < 1 s | SIGTERM → offline |
| Graceful shutdown, busy worker | ≤ grace (20 s) + release ~10 ms | job back to `pending` with attempt refunded |

## What benchmarking found (and fixed)

The very first run measured **~2 jobs/s** on one worker — 150× below
expectation. Root cause: when a claim filled every concurrency slot, the
consume loop blocked on the Redis wake channel for the full 2 s poll
interval, and nothing woke it when an in-flight job finished. Fast jobs
therefore dispatched at `concurrency / poll_interval` (4 per 2 s) regardless
of capacity. The fix (`runner.py`): job-completion callbacks set a
`slot_freed` event, and the idle branch waits on *wake OR freed slot OR poll
timeout*, whichever fires first. Same run, same hardware, after the fix:
**309 jobs/s** — a 150× improvement that unit tests and fault injection had
no chance of catching. Throughput claims that were never measured are
fiction; this one was fiction until 2026-07-04.

## Caveats

Single host: workers, Postgres, and Redis share CPU, so the 8-worker row
understates what a real fleet against a dedicated Postgres would do, and all
numbers include compose networking. Percentiles come from `percentile_cont`
over per-job timestamps written by Postgres itself (`now()` at claim and
finalize), so they inherit ~ms timestamp resolution but no client clock
skew. Echo jobs are the scheduler's worst case per unit of useful work —
real workloads with seconds-long handlers hit the concurrency ceiling
(`workers × WORKER_CONCURRENCY`) long before the claim ceiling.
