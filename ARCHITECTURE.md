# Architecture

This document explains how Chronos works and, more importantly, **why it is
built this way** — every significant decision is stated with the tradeoff that
was accepted.

## System overview

```
                       ┌────────────────────┐
   ┌──────────┐  HTTP  │   FastAPI (api)    │
   │ Next.js  │───────▶│  routers→services  │
   │ dashboard│        │   →repositories    │
   └──────────┘        └─────────┬──────────┘
                                 │ SQL (transactions)
                                 ▼
        wake pub/sub   ┌────────────────────┐
   ┌───────┐  ┌───────▶│     PostgreSQL     │  source of truth:
   │ Redis │◀─┘        │ jobs / attempts /  │  queue state, leases,
   └───┬───┘           │ workers / users    │  retry policy, DLQ
       │ subscribe     └─────────▲──────────┘
       ▼                         │ FOR UPDATE SKIP LOCKED
   ┌────────────────────────────┴───────────┐
   │            worker fleet (×N)           │
   │ consume loop · heartbeat loop · reaper │
   └────────────────────────────────────────┘
```

Three process roles share one codebase and one image:

- **API** — HTTP edge. Enqueues jobs, serves reads, never executes work.
- **Worker** (×N, horizontally scalable) — claims, executes, finalizes jobs;
  heartbeats; hosts a reaper loop.
- **Reaper** — not a separate process: every worker runs the sweep, but a
  Postgres advisory lock guarantees only one sweep executes cluster-wide at a
  time. Zero extra infrastructure, no single point of failure, no leader
  election protocol to get wrong.

## Decision 1 — PostgreSQL as the queue, not Redis/RabbitMQ

**Choice**: job state lives in a `jobs` table; claiming uses
`SELECT … FOR UPDATE SKIP LOCKED`. Redis is only a wake-up channel and a
3-second stats cache.

**Why**:
- *Transactional enqueue and state transitions.* A job row, its attempt audit
  record, and its state change commit atomically. With a Redis/AMQP broker you
  immediately face the dual-write problem (DB row committed but broker message
  lost, or vice versa) and need an outbox pattern — more machinery than the
  problem warrants.
- *Queryability.* The DLQ, per-status counts, attempt history, and lease
  inspection are plain SQL. Brokers make "show me everything about job X"
  genuinely hard.
- *Exactly-one claim semantics.* `SKIP LOCKED` gives contention-free atomic
  hand-off without inventing a locking protocol.

**Tradeoff accepted**: a Postgres queue tops out around thousands of
claims/second — far below Kafka/Redis Streams territory — and polling adds
latency. The latency cost is mitigated by the Redis wake channel (enqueue →
publish → idle workers claim immediately); the throughput ceiling is the right
trade for a system whose jobs take seconds, not microseconds. The scaling path
(partitioning, archival) is noted at the bottom.

**Redis failure mode**: every Redis call degrades silently — publish failures
are logged and dropped, subscribers fall back to interval polling, the stats
cache becomes a pass-through. Redis going down costs up to
`POLL_INTERVAL_SECONDS` of latency and nothing else. This is deliberate: the
optimization layer must never become a correctness dependency.

## Decision 2 — at-least-once execution + idempotency, not exactly-once

Exactly-once *execution* is impossible in a distributed system (a worker can
always die after doing the work but before acknowledging it). Chronos is
honest about this:

- Execution is **at-least-once**: a lease expiry can resurrect a job whose
  worker actually finished the side effect but died before the guarded
  `UPDATE` committed.
- **Enqueue is exactly-once** via idempotency keys: a unique partial index on
  `(owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL` makes
  duplicate submissions return the existing job (HTTP 200 vs 201). The
  pre-check is a fast path only; the index is the authority and the
  `IntegrityError` race is handled.
- Handlers receive `job_id` in their `TaskContext` as the natural scope for
  making their own side effects idempotent.

**Tradeoff accepted**: duplicate side effects are possible in the
crash-after-success window. The alternative (at-most-once: acknowledge before
executing) silently loses work, which is worse for almost every real workload.

## Decision 3 — leases + heartbeats, DB clock as the only clock

A claim writes `locked_by = worker_id` and `lease_expires_at = now() + 30s`.
The worker's heartbeat loop (every 10s) extends all of its leases and its own
`last_heartbeat_at` in **one transaction** — liveness and leases can't drift
apart. If a worker dies, its leases stop moving and the reaper reclaims them.

Details that matter:

- **All time arithmetic happens in Postgres** (`now()`), never on worker
  clocks. Clock skew between containers cannot cause premature reclaims or
  immortal leases.
- **`LEASE_SECONDS = 3 × HEARTBEAT_SECONDS`**: one missed heartbeat (GC pause,
  network blip, transient DB error) never costs a lease; three in a row is
  legitimate evidence of death.
- **Fencing without fencing tokens**: every transition out of RUNNING is a
  guarded UPDATE — `WHERE id = :id AND status = 'running' AND locked_by =
  :worker`. A worker whose lease was reclaimed gets rowcount 0 on its own
  completion attempt, logs `lease_lost`, and crucially *cannot overwrite* the
  reaper's decision. The (status, locked_by) pair plays the role of a fencing
  token: stale actors are rejected by the WHERE clause, not by convention.

**Tradeoff accepted**: heartbeat traffic (1 UPDATE per worker per 10s —
negligible) and a worst-case detection latency of `LEASE_SECONDS +
REAPER_INTERVAL_SECONDS` (~35s) before a dead worker's jobs are rescheduled.

## Decision 4 — the job state machine and where retries live

```
                        ┌────────────── cancel ──────────────┐
                        │                                     ▼
 enqueue ──▶ PENDING ──claim──▶ RUNNING ──success──▶ SUCCEEDED   CANCELLED
               ▲  ▲               │   │                              │
               │  │   fail w/ budget  │ fail, budget exhausted      │
               │  └───(backoff)───┘   │ (also: lease lost on        │
               │                      ▼  final attempt)             │
               │        release      DEAD  ◀── the DLQ ──▶ requeue ─┤
               └──(shutdown, refund)──┘         (fresh budget)──────┘
```

- A retry is **not a separate state**: it's `PENDING` with `attempt_count > 0`
  and `run_at` set to `now() + backoff`. The claim query's `run_at <= now()`
  predicate *is* the retry scheduler — no timer wheels, no delayed-message
  plumbing, nothing extra to crash.
- Backoff is exponential with a cap and up-to-20% jitter (thundering-herd
  protection when a downstream outage fails hundreds of jobs simultaneously).
  The policy is **denormalized onto the job row** so changing defaults never
  retroactively alters in-flight jobs.
- **Every attempt has a wall-clock budget** (`timeout_seconds`, default
  300s): the worker wraps the handler in `asyncio.wait_for`, and a timeout
  is treated as a normal failure — it burns the attempt, records its reason
  on the attempt row, and follows the same retry/DLQ decision. This bounds
  the hung-handler case: without it, the heartbeat would extend a stuck
  job's lease forever. Caveat: cancellation is cooperative — a handler that
  swallows `CancelledError` can still hang; that residue is what the
  process-pool lane (scaling path) addresses.
- **Lease expiry counts as a failed attempt.** A poison job that crashes its
  worker converges to the DLQ instead of cycling through the fleet forever.
- **Graceful-shutdown release is the one exception**: a job aborted because
  *the worker* was shutting down gets its attempt refunded and runs again
  immediately. The interruption wasn't the job's fault; burning budget on it
  would be unjust and operationally surprising during deploys.
- The **DLQ is a view** (`status = 'dead'`), not a separate table/queue. A
  dead job keeps its identity, payload, error, and full attempt history;
  requeueing is a guarded transition, not a copy.

The retry decision itself (`decide_failure`) is a **pure function** in
`app/domain/` shared by the worker's failure path and the reaper — one source
of truth, unit-tested without a database.

## Decision 5 — the claim query

```sql
WITH candidates AS (
    SELECT id FROM jobs
    WHERE status = 'pending' AND run_at <= now() AND queue = ANY(:queues)
    ORDER BY priority DESC, run_at, created_at
    LIMIT :free_slots
    FOR UPDATE SKIP LOCKED
)
UPDATE jobs
SET status = 'running', locked_by = :worker, attempt_count = attempt_count + 1,
    started_at = now(), lease_expires_at = now() + :lease
FROM candidates WHERE jobs.id = candidates.id
RETURNING jobs.*;
```

- `SKIP LOCKED` makes N concurrent claimers partition the ready set instead of
  blocking on or double-claiming the same rows.
- The CTE form (rather than `WHERE id IN (subquery)`) pins the locking SELECT
  to exactly one evaluation.
- Claim order is `effective_priority DESC, run_at, created_at` where
  effective priority = `priority` + 1 point per `PRIORITY_AGING_INTERVAL_
  SECONDS` waited (capped at `PRIORITY_AGING_MAX_BOOST`). Aging bounds
  starvation: a sustained stream of high-priority work can delay a low-
  priority job by at most interval × boost (~3.3h at defaults), never
  forever. Cost: the ready set is sorted on a computed key, so the claim
  index filters but no longer pre-sorts — fine while the ready set is small,
  which the partial index guarantees is the common case.
- The attempt audit row is inserted in the same transaction: a claim without
  an audit trail cannot exist.
- The supporting index is **partial** (`WHERE status = 'pending'`), so the hot
  index stays small no matter how many millions of terminal jobs accumulate.

An integration test (`tests/test_claim_concurrency.py`) runs 8 concurrent
claimers over 50 jobs and asserts every job is claimed exactly once.

## Decision 6 — graceful shutdown protocol

On SIGTERM the worker:

1. Sets a draining flag (stops claiming) and marks itself `DRAINING` in the
   fleet table — visible on the dashboard mid-deploy.
2. Waits up to `SHUTDOWN_GRACE_SECONDS` for in-flight jobs; heartbeats
   continue meanwhile so their leases stay live.
3. Cancels whatever remains and **releases** those jobs: back to `PENDING`
   with `run_at = now()` and the attempt refunded (audit row closed as
   `ABORTED`). This beats the alternative — letting the lease expire — by
   ~35 seconds of job latency per deploy, and beats counting the attempt by
   not punishing jobs for operator actions.
4. Marks itself `OFFLINE` and exits. Compose's `stop_grace_period` is set
   above the drain window so Docker never SIGKILLs a draining worker.

If a worker is SIGKILLed anyway (OOM, node loss), the lease/reaper path covers
it — shutdown handling is an optimization, crash recovery is the guarantee.

## Database design

```
users 1───∞ jobs ∞───1 workers (locked_by, nullable)
              1
              │
              ∞
        job_attempts ∞───1 workers (nullable)
```

**jobs** is a compact state-machine record: identity (owner, queue, task,
payload JSONB), scheduling (status, priority, run_at), retry policy (4
denormalized columns with CHECK constraints), lease (locked_by,
lease_expires_at), and outcome (result, last_error). Indexes are designed per
query, not per column:

| Index | Kind | Serves |
|---|---|---|
| `ix_jobs_claim (priority DESC, run_at) WHERE status='pending'` | partial | the claim CTE — stays tiny forever |
| `ix_jobs_lease (lease_expires_at) WHERE status='running'` | partial | reaper sweep |
| `uq_jobs_owner_idempotency` | unique partial | idempotent enqueue |
| `ix_jobs_owner_created (owner_id, created_at DESC)` | btree | owner-scoped listing |

**job_attempts** is an append-only audit log — one row per claim, closed as
`succeeded / failed / lost / aborted`. Separating it keeps the job row small
and update-hot while history stays queryable (it also feeds the throughput
chart). Attempt rows are only ever closed via `WHERE status = 'running'`
guards, so a reaper and a slow worker can't both write the verdict.

**workers** is the fleet registry (`online / draining / offline`) — purely
observational; correctness never depends on it (leases carry the truth).

Native PG enums (`job_status`, `attempt_status`, `worker_status`) make illegal
states unrepresentable at the storage layer; CHECK constraints bound every
numeric policy field. All constraints/indexes are explicitly named via a
naming convention, and the initial Alembic migration is hand-written and
reviewable.

## Backend architecture

```
api/routers   → HTTP edge: DTO validation, auth, error mapping. No logic.
services      → business rules & transactions. No HTTP, no raw SQL.
repositories  → all SQL. Guarded UPDATEs, the claim CTE. No policy.
domain        → pure functions (retry decisions). No I/O at all.
models        → SQLAlchemy 2.0 typed models = schema source of truth.
schemas       → Pydantic DTOs; ORM objects never cross the HTTP boundary.
```

- Services raise typed exceptions (`NotFoundError`, `ConflictError`, …); only
  routers translate them to HTTP statuses. The worker consumes the same
  service layer with FastAPI entirely absent from its stack.
- The API uses request-scoped sessions via DI; the worker's
  `ExecutionService` owns a session factory and opens a short transaction per
  operation — a long-running job never pins a DB connection.
- Structured logging (structlog, JSON) binds `request_id` per HTTP request and
  `worker_id`/`job_id`/`attempt` per execution, so one grep follows a job
  across API and workers.

## Failure-mode walkthrough

| Failure | What happens | Guarantee |
|---|---|---|
| Worker SIGKILL mid-job | heartbeats stop → lease expires → reaper closes attempt as LOST, reschedules with backoff (or DLQ if budget spent) | no lost jobs; poison jobs converge to DLQ |
| Handler exceeds its time budget | `asyncio.wait_for` cancels it at `timeout_seconds`; attempt recorded FAILED with the timeout reason; normal retry/DLQ decision | hung handlers can't pin a slot forever |
| Worker finishes but lease already reclaimed | guarded UPDATE hits 0 rows → worker logs `lease_lost`, discards its result | reaper's verdict is never overwritten; duplicate side effects possible (documented at-least-once) |
| Two workers claim simultaneously | `SKIP LOCKED` partitions the ready set | a job is never handed out twice |
| Duplicate enqueue (client retry) | unique partial index → existing job returned | one job per idempotency key |
| Redis down | wake channel and stats cache degrade; workers poll | latency +≤2s; zero correctness impact |
| Postgres down | API 503s via `/readyz`; workers back off and retry claims | system resumes when PG returns; no state lost |
| Deploy (SIGTERM) | drain → finish or release with refund | jobs rerun immediately, no budget burned |
| Reaper host dies | any other worker's reaper loop wins the advisory lock next sweep | no dedicated scheduler to lose |
| Worker stalls > 60s but survives (host sleep, paused container) | reaper marks it offline; its next successful heartbeat resurrects it (offline → online, stopped_at cleared) | fleet view self-heals from false death verdicts |

## Scaling path (deliberately not built)

Chosen-simple things and what their production evolution looks like:

- **Completed-job growth** → partition `jobs` by status/time or archive
  terminal rows to a history table; the partial indexes already isolate the
  hot set.
- **Claim contention beyond ~10³/s** → per-queue sharding (a first cut is
  built: queues carry a `shard_key` and a worker started with
  `WORKER_SHARD` claims only its shard), then a purpose-built broker with
  the outbox pattern.
- ~~**Cron/recurring jobs**~~ → **built**: a `schedules` table materializes
  `jobs` rows via an advisory-locked sweep (same pattern as the reaper);
  the claim path is unchanged, firing is exactly-once via the idempotency
  index, and missed ticks collapse into a single run.
- **Multi-tenant fairness** → per-owner token buckets in the claim CTE.
- **Cancellation of RUNNING jobs** → cooperative cancel flags checked by
  handlers; not faked here — killing coroutines mid-side-effect is worse than
  not offering the feature.
