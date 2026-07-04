# Chronos — Project Report

A distributed job scheduler: PostgreSQL as the transactional queue of record,
lease-based worker liveness, at-least-once execution with idempotent enqueue,
retry policies with a dead letter queue, and verified crash recovery.

---

## 1. Executive summary

Chronos executes background jobs reliably across a fleet of worker processes.
Clients enqueue jobs over an authenticated HTTP API; workers claim jobs
atomically from PostgreSQL, execute them with a bounded lease, and either
finalize them or hand them back to the retry pipeline. Jobs that exhaust
their retry budget land in a dead letter queue with full attempt history,
inspectable and requeueable from a dashboard.

The project optimizes for correctness under failure, not feature count.
Every claim is atomic (`FOR UPDATE SKIP LOCKED`), every state transition is a
guarded UPDATE that restates its precondition (a stale worker physically
cannot overwrite a newer decision), and all lease arithmetic uses the
database clock so process clock skew cannot corrupt liveness decisions.

The following behaviors were **verified against the running system**, not
just unit-tested: exactly-once claim distribution under 8 concurrent
claimers; deterministic retry-until-success; DLQ delivery after budget
exhaustion; idempotent enqueue under the `Idempotency-Key` header (201 then
200, same job id); SIGKILL crash recovery (lease reclaimed, attempt recorded
as `lost`, job retried on a surviving worker); and graceful SIGTERM drain
(in-flight job released back to pending with its attempt refunded).

Scope is deliberate: single Postgres, single-team trust model, no cron
schedules, no cancellation of running jobs. Section 8 states the scaling
ceilings and the ordered plan past them.

**Stack:** FastAPI, SQLAlchemy 2 (async), PostgreSQL 16, Redis 7, Next.js 15,
Docker Compose. ~3,500 lines of application code, 9 tests, two design docs.

---

## 2. Architecture overview

Three process roles share one codebase and one container image:

- **API** — HTTP edge. Validates DTOs, enforces ownership, enqueues and
  reads. Never executes jobs.
- **Workers (×N)** — claim, execute, finalize. Each runs three concurrent
  loops on one asyncio event loop: consume, heartbeat, reaper.
- **Reaper** — not a separate process. Every worker hosts the sweep; a
  Postgres advisory lock ensures exactly one runs cluster-wide per tick.
  No leader election, and no dedicated scheduler process to lose.

Backend layering, with dependencies pointing strictly inward:

```
api/routers   HTTP edge: DTO validation, auth, error mapping. No logic.
services      business rules and transactions. No HTTP, no raw SQL.
repositories  all SQL: the claim CTE, guarded UPDATEs. No policy.
domain        pure functions (retry decisions). No I/O.
models        SQLAlchemy 2 typed models — the schema's source of truth.
```

Services raise typed exceptions; only routers translate them to HTTP status
codes. The worker consumes the same service layer with FastAPI absent from
its process — evidence the layering is real, not decorative.

**PostgreSQL is the single source of truth**: queue state, leases, retry
policy, attempt history, users, workers. **Redis is a latency optimization
only** — a pub/sub wake channel so idle workers claim immediately instead of
waiting out a 2s poll, plus a 3s stats cache. Every Redis call degrades
silently to polling; Redis down costs ≤2s of latency and nothing else.

Sessions are scoped to the unit of work: request-scoped in the API
(dependency-injected), short transaction-per-operation in workers — a
long-running job never pins a database connection.

---

## 3. Reliability overview

**Delivery model.** Execution is at-least-once, stated honestly: a worker
can always die after performing a side effect but before committing
`succeeded`. The complementary guarantees: enqueue is exactly-once (unique
partial index on `(owner_id, idempotency_key)`), and *state transitions* are
exactly-once (guarded UPDATEs). Handlers receive the `job_id` as the natural
scope for making their own side effects idempotent.

**Leases and heartbeats.** A claim stamps `locked_by` and
`lease_expires_at = now() + 30s`. Every 10s the worker extends all of its
leases and its own liveness timestamp in one transaction — the two facts
cannot drift apart. The 3:1 lease-to-heartbeat ratio means one missed
heartbeat (GC pause, network blip) never causes a spurious reclaim; three
consecutive misses is real evidence of death.

**Recovery.** The reaper sweeps every 5s: expired leases are reclaimed, the
attempt is closed as `lost`, and the shared retry-decision function either
reschedules with backoff or moves the job to the DLQ. Lease expiry burns an
attempt deliberately — a poison job that kills its workers must converge to
the DLQ, not cycle through the fleet forever. Worst-case detection latency
is lease + sweep interval ≈ 35s.

**Graceful shutdown.** SIGTERM → stop claiming, announce `draining`, wait up
to 20s for in-flight jobs (heartbeats continue, so leases stay alive
mid-drain), then cancel the remainder and *release* them: back to `pending`
immediately with the attempt refunded — an operator action shouldn't burn a
job's budget. SIGKILL skips all of this and degrades safely into the lease
path. Graceful shutdown is a latency optimization; crash recovery is the
guarantee.

**Failure modes, summarized:**

| Failure | Outcome |
|---|---|
| Worker SIGKILL mid-job | lease expires → attempt `lost` → retry or DLQ |
| Worker finishes after lease reclaimed | guarded UPDATE hits 0 rows; result discarded; reaper's verdict stands |
| Duplicate enqueue (client retry) | unique index returns existing job |
| Redis down | polling fallback; ≤2s added latency; zero correctness impact |
| Postgres down | API 503s readiness; workers back off and retry; nothing lost |
| Deploy (SIGTERM) | drain, release, refund; jobs rerun immediately |

---

## 4. Concurrency overview

**Atomic claim.** One statement claims up to N ready jobs:

```sql
WITH candidates AS (
  SELECT id FROM jobs
  WHERE status = 'pending' AND run_at <= now() AND queue = ANY(:queues)
  ORDER BY priority DESC, run_at, created_at
  LIMIT :free_slots
  FOR UPDATE SKIP LOCKED
)
UPDATE jobs SET status = 'running', locked_by = :worker,
  attempt_count = attempt_count + 1, lease_expires_at = now() + :lease, ...
FROM candidates WHERE jobs.id = candidates.id
RETURNING jobs.*;
```

`SKIP LOCKED` makes concurrent claimers partition the ready set instead of
blocking or double-claiming. The CTE (rather than `WHERE id IN (subquery)`)
pins the locking SELECT to exactly one evaluation. The attempt audit row is
inserted in the same transaction — an unaudited claim cannot exist.

**Fencing without tokens.** Every transition out of `running` restates its
precondition: `WHERE id = :id AND status = 'running' AND locked_by = :me`.
A worker whose lease was reclaimed gets rowcount 0 on its own completion,
logs `lease_lost`, and cannot overwrite the reaper's decision. The
`(status, locked_by)` pair plays the role of a fencing token whose
comparison executes inside the same ACID domain as the write. There are no
blind writes anywhere in the system, which is what makes every
reaper-vs-worker interleaving analyzable: whoever commits second finds their
precondition false and becomes a no-op.

**Reaper singleton.** `pg_try_advisory_xact_lock` gates the sweep: any
worker may try, one wins per tick, and the lock dies with the transaction —
a reaper that crashes mid-sweep releases the lock by crashing. Failover is
implicit: any surviving worker wins the next 5s tick.

**Worker model.** One asyncio event loop per process, up to
`WORKER_CONCURRENCY` handler coroutines. Every attempt runs under
`asyncio.wait_for(handler, job.timeout_seconds)` (default 300s): a timeout
is a normal failure — attempt burned, reason recorded, same retry/DLQ
decision — so a hung handler cannot pin a concurrency slot behind an
ever-renewing lease. Remaining limitation, stated openly: a *CPU-bound*
handler starves the event loop, heartbeats stop, and the lease can expire
under a still-running job — duplicate side effects (state stays consistent
via the guards). CPU-heavy work belongs in a process pool; see section 8.

---

## 5. Database overview

Four tables. `jobs` is a compact state-machine record; `job_attempts` is an
append-only audit log (one row per claim, closed as
`succeeded | failed | lost | aborted`); `workers` is observational fleet
state — correctness never reads it; `users` owns jobs.

**State machine, enforced by guarded transitions:**
`pending → running → succeeded | pending (retry) | dead`, plus
`pending → cancelled` and `dead/cancelled → pending` (manual requeue).
A retry is not a separate status: it is `pending` with a future `run_at` —
the claim predicate `run_at <= now()` *is* the retry scheduler, so there is
no timer infrastructure to build or crash.

**Indexes are per-query, not per-column** — the table is write-hot and every
index taxes claim/heartbeat/finalize:

| Index | Serves |
|---|---|
| `(priority DESC, run_at) WHERE status='pending'` (partial) | the claim CTE; size tracks *backlog*, not history — claiming stays fast at any table size |
| `(lease_expires_at) WHERE status='running'` (partial) | reaper sweep |
| `(owner_id, idempotency_key)` unique partial | exactly-once enqueue; the index *is* the correctness mechanism |
| `(owner_id, created_at DESC)` | owner-scoped listing |

Native enums (`job_status`, `attempt_status`, `worker_status`) make illegal
states unrepresentable at the storage layer; CHECK constraints bound every
retry-policy field. Retry policy is denormalized onto the job row so a
default change never retroactively alters in-flight jobs. All constraints
carry deterministic names via a naming convention; the initial Alembic
migration is hand-written and reviewable.

All time arithmetic (`run_at` comparisons, lease expiry, backoff scheduling)
executes in Postgres via `now()` — the database clock is the only clock, so
container clock skew cannot produce premature reclaims or immortal leases.

---

## 6. Design decisions

Each decision names the alternative it rejected and the cost it accepted.

**Postgres as the queue, not Redis/RabbitMQ/SQS.** Enqueue and every state
transition commit atomically with the job's data — no dual-write problem,
no outbox machinery. The DLQ, counts, and attempt history are plain SQL.
Cost: a claim-throughput ceiling around thousands/sec and polling latency
(mitigated by the Redis wake channel). Right trade for jobs measured in
seconds.

**At-least-once + idempotency, not exactly-once.** Exactly-once execution
is impossible across failure domains (crash after side effect, before ack).
At-most-once silently loses work — worse for nearly every workload. Chronos
picks the honest option and gives callers the tools (idempotency keys,
`job_id` in handler context) to neutralize duplicates.

**DB clock as the only clock.** Lease math on worker clocks breaks under
skew. Every liveness decision evaluates `now()` inside Postgres. Cost: none
measurable.

**Lease expiry burns an attempt; shutdown release refunds one.** Poison
jobs must converge to the DLQ (burn); deploys must not punish innocent jobs
(refund). The `lost` vs `aborted` attempt statuses let an operator tell the
two apart at a glance.

**Reaper inside workers behind an advisory lock, not a scheduler service.**
One less deployable, no single point of failure, implicit failover. Cost:
reaper capacity is coupled to worker deployment.

**DLQ as a status, not a separate table.** A dead job keeps its payload,
error, and full attempt trail; requeue is one guarded transition rather
than a cross-table move. Cost: terminal rows share the hot table until an
archival policy exists (section 8).

**No cancellation of running jobs.** Killing coroutines mid-side-effect is
worse than not offering the feature. Pending jobs cancel cleanly; running
jobs finish their attempt.

---

## 7. Testing and verification summary

**Unit tests (no infrastructure).** The retry policy is pure — 8 tests pin
the exponential curve, the cap, the jitter bound (≤20%), DLQ on budget
exhaustion, and the single-attempt edge case. Both the worker failure path
and the reaper call this same function, so the tests cover both actors.

**Concurrency integration test (real Postgres).** Eight concurrent claimers
race over 50 jobs in batches of 5; the test asserts the union of claims is
exactly the job set with no duplicates — the exactly-once-claim invariant
under real lock contention. It isolates itself on a unique queue name (so a
live fleet can share the database) and cleans up its rows.

**Live fault injection (run against the composed stack, results observed in
the database):**

| Scenario | Observed result |
|---|---|
| `demo.fail_until` (fails twice, then succeeds) | succeeded on attempt 3; attempts 1–2 recorded `failed` with tracebacks |
| `demo.always_fail`, budget 2 | `dead` after 2 attempts; visible in DLQ; requeue works |
| Same `Idempotency-Key` twice | HTTP 201 then 200; identical job id; one row |
| SIGKILL worker holding a 90s job | attempt 1 closed `lost` ~35s later; job retried; attempt 2 `succeeded` on the surviving worker |
| SIGTERM workers holding a 300s job | drain logged; job released to `pending` with `attempt_count` back to 0; attempt closed `aborted`; workers marked `offline` |
| 8 fake claimers abandoned with live leases | reaper reclaimed all 50 leases and marked the stale workers `offline` |

**Benchmarks** (see `docs/BENCHMARKS.md`): 309 → 614 → 1,014 → 1,505 jobs/s
at 1/2/4/8 workers (linear to 2×, contention-bent beyond); 35 ms p50
enqueue-to-claimed dispatch on an idle fleet; 6–10 ms execute+finalize. The
first benchmark run caught a real 150× dispatch-latency bug (saturated
workers waited out the poll interval instead of re-claiming when a slot
freed) — fixed and re-measured.

**What is deliberately not tested:** handler business logic (demo tasks are
scaffolding) and long soak runs; benchmarks are single-host bursts.

---

## 8. Scaling limitations and future improvements

**Known limits, quantified:**

- **Claim throughput** tops out around low thousands/sec: write
  amplification (≥3 UPDATEs per job lifetime) creates dead tuples faster
  than autovacuum reclaims them, and claimers contend on the queue head.
  The partial claim index delays this — it scales with backlog, not history.
- **Table growth is unbounded.** Terminal `jobs` and `job_attempts` rows
  accumulate; the global stats count degrades first (full-table group-by
  every cache miss).
- **Failure detection is ~35s** (lease 30s + sweep 5s). Tunable, but the
  3:1 heartbeat invariant sets a floor.
- **Mass-death recovery** drains at ~20 leases/sec (100 per 5s sweep).
- **Single Postgres** is the availability ceiling: it down = system down
  (by design — it is the source of truth).

**Improvement plan, in order of value per unit effort:**

1. ~~Per-job execution timeout~~ — **implemented**: `timeout_seconds`
   column, `asyncio.wait_for` in the worker, timeout = normal failure.
2. ~~Restart policies; JWT-secret fail-fast; payload cap; lockfiles~~ —
   **implemented**: `restart: unless-stopped` everywhere, boot refusal on
   the default secret outside development, 64KB payload limit, pinned
   `requirements.lock` + `package-lock.json` (`npm ci`).
3. ~~Metrics endpoint~~ — **implemented**: Prometheus `/metrics` with queue
   depth, oldest-ready-job age, claim wait, heartbeat lag, lease
   reclaims/min, DLQ size; worker container healthcheck keyed to heartbeat
   recency. Alert *rules* remain deployment work.
4. **Retention:** archive terminal rows; replace global counts with a
   transition-maintained counter table.
5. **Process-pool execution lane** for CPU-bound handlers — removes the
   event-loop-starvation duplicate-execution window.
6. **Per-queue claim sharding**, then partitioning — each buys roughly an
   order of magnitude before
7. **a dedicated broker with an outbox table** — the last resort, because it
   reintroduces the dual-write problem the current design eliminates.

Priority starvation is also now bounded (aging in the claim query), login
is throttled per-IP, and the throughput ceiling is **measured, not argued**:
309 → 1,505 jobs/s from 1 → 8 workers with contention visibly bending the
curve — see `docs/BENCHMARKS.md`, including the 150× dispatch-latency bug
the first benchmark run caught.

The sequencing is the point: every step preserves the system's core
property — state transitions that are transactional, guarded, and auditable
— and the expensive migration is deferred until the cheap steps are
exhausted.
