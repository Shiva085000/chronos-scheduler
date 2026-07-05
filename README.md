# Chronos — a production-inspired distributed job scheduler

Chronos is a distributed job scheduler built the way real queueing systems
(Temporal, SQS consumers, Sidekiq/Faktory, `pg`-backed queues like Oban) are
built: **PostgreSQL as the transactional queue of record**, lease-based worker
liveness, at-least-once execution with idempotency support, configurable
retries (fixed / linear / exponential backoff), and a dead letter queue —
behind a clean FastAPI service layer and a Next.js operations dashboard.

Work is organized **organization → project → queue**: queues are first-class
configuration objects (pause/resume, fleet-wide concurrency caps, shard keys,
default retry policy), and jobs can be immediate, delayed (`run_at`),
recurring (cron schedules), batched (atomic, up to 100), or chained into
workflows via dependencies. RBAC (owner/admin/member/viewer) guards the API,
and DLQ failures get optional AI root-cause summaries.

📐 The design rationale and every tradeoff is documented in
[ARCHITECTURE.md](ARCHITECTURE.md), with the one-page decision defense in
[docs/WHY.md](docs/WHY.md). For evaluators:
[docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md) is an eight-section report
(architecture, reliability, concurrency, database, decisions, verification,
scaling limits) and [docs/DIAGRAMS.md](docs/DIAGRAMS.md) holds eleven Mermaid
diagrams of the core protocols, including the ER diagram (§11). Everything
combined, print-ready: [docs/Chronos-Report.pdf](docs/Chronos-Report.pdf)
(design doc + measured benchmarks + rendered diagrams, 23pp).

## Live demo

| What | Where |
|---|---|
| Dashboard | https://frontend-production-e4f9.up.railway.app — click **Demo Login** |
| API (Swagger) | https://api-production-f587.up.railway.app/docs |

Hosted on Railway (managed Postgres + Redis + API + worker). The full
topology — two workers plus the dedicated shard worker — runs locally:

## Quickstart

```bash
docker compose up --build -d      # postgres, redis, api, 2 workers, frontend
docker compose exec api python -m app.scripts.seed   # demo user + demo jobs
```

| What | Where |
|---|---|
| Dashboard | http://localhost:3000 — click **Demo Login**, or `demo@example.com` / `demo12345` (read-only RBAC demo: `viewer@example.com` / `viewer12345`) |
| OpenAPI docs (Swagger) | http://localhost:8000/docs |
| API | http://localhost:8000/api/v1 |

If 8000/3000 are taken on your machine, set `API_PORT`/`WEB_PORT` in `.env`
(see `.env.example`) and rerun `docker compose up --build -d`.

The seed enqueues one job of each demo task type, so within a minute you can
watch: an instant success, a 10s job holding a lease, a job that fails twice
and then succeeds (retry pipeline), a flaky job, and a job that exhausts its
budget and lands in the DLQ — requeue it from the dashboard. It also creates
a `capped` queue (`max_concurrency=1`) fed by an atomic 3-job batch (watch
them run strictly one at a time however many workers idle), a `*/2 * * * *`
cron schedule, an A → B → C workflow whose steps unlock as predecessors
succeed, a `sharded` queue consumed only by the dedicated shard worker, and
a read-only viewer user to demo RBAC denials.

## What's demonstrated

| Requirement | Where to look |
|---|---|
| Atomic job claiming | `SELECT … FOR UPDATE SKIP LOCKED` CTE in [backend/app/repositories/jobs.py](backend/app/repositories/jobs.py) (`claim_batch`) |
| Heartbeats | worker loop in [backend/app/worker/runner.py](backend/app/worker/runner.py); one transaction extends worker liveness + all job leases |
| Lease expiration & worker recovery | reaper in [backend/app/services/execution_service.py](backend/app/services/execution_service.py) (`reap_once`), advisory-locked so exactly one sweep runs cluster-wide |
| Retry policies | pure domain logic in [backend/app/domain/retry.py](backend/app/domain/retry.py) — fixed / linear / exponential strategies, cap, jitter; per-job policy columns inherited from queue defaults |
| Queue configuration | [backend/app/api/routers/queues.py](backend/app/api/routers/queues.py) — pause/resume, per-queue stats; concurrency caps enforced atomically under the queue row lock in `claim_batch` |
| Recurring (cron) jobs | `schedules` table + advisory-locked materializer in [backend/app/services/execution_service.py](backend/app/services/execution_service.py) (`materialize_schedules_once`) — exactly-once firing, missed ticks collapse |
| Batch jobs | `POST /jobs/batch` — one transaction, all-or-nothing, shared `batch_id` |
| Tenancy | organizations → projects → queues ([backend/app/models/tenancy.py](backend/app/models/tenancy.py)); personal org + default project created at registration |
| Workflow dependencies | `job_dependencies` table; the claim scan skips jobs whose prerequisites haven't succeeded ([backend/app/repositories/jobs.py](backend/app/repositories/jobs.py)) |
| Queue sharding | queues carry `shard_key`; the `worker_sharded` compose node claims only `WORKER_SHARD=1` |
| RBAC | owner/admin/member/viewer hierarchy in [backend/app/services/rbac.py](backend/app/services/rbac.py), enforced per endpoint |
| AI failure summaries | [backend/app/services/ai_summary_service.py](backend/app/services/ai_summary_service.py) — Gemini root-cause notes on DLQ jobs, cached by error hash, degrades gracefully without a key |
| Per-job execution timeout | `timeout_seconds` (default 300) enforced via `asyncio.wait_for` in [backend/app/worker/runner.py](backend/app/worker/runner.py); a timeout is a normal failure feeding the same retry/DLQ path |
| Dead Letter Queue | `status = dead` + DLQ endpoints in [backend/app/api/routers/dlq.py](backend/app/api/routers/dlq.py), requeue from the UI |
| Idempotency | unique partial index `(owner_id, idempotency_key)`; `Idempotency-Key` header on `POST /jobs` |
| Graceful shutdown | SIGTERM → drain → finish in-flight within grace → release the rest back to PENDING with the attempt refunded |
| Structured logging | structlog JSON with request/worker/job correlation ids |
| Metrics | Prometheus `/metrics`: queue depth, **oldest-ready-job age**, claim wait, heartbeat lag, lease reclaims, DLQ size |
| Starvation bound | priority aging in the claim query — +1 effective priority per minute waited, capped |
| Measured performance | [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — 309→1,505 jobs/s from 1→8 workers, 35ms p50 dispatch |
| Clean architecture | routers → services → repositories → models; DTOs at the edge; HTTP-free service layer |

## Try the reliability story by hand

```bash
# 1. Get a token
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -d 'username=demo@example.com&password=demo12345' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. Idempotency: run this twice — same job id both times, second returns 200 not 201
curl -si localhost:8000/api/v1/jobs -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: my-unique-key-1" -H "Content-Type: application/json" \
  -d '{"task_name":"demo.echo","payload":{"message":"exactly once, effectively"}}' | head -1

# 3. Worker crash recovery: enqueue a 60s job, then SIGKILL a worker mid-run.
curl -s localhost:8000/api/v1/jobs -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"task_name":"demo.sleep","payload":{"seconds":60}}'
docker compose kill worker   # no graceful shutdown — leases go stale
# Within ~35s (lease 30s + reaper sweep) the job is back to PENDING and the
# dashboard shows the attempt as "lost". Restart workers to see it finish:
docker compose up -d worker

# 4. Graceful shutdown (contrast with kill): watch the worker drain instead
docker compose stop worker   # SIGTERM → finishes/releases jobs, marks itself offline
docker compose up -d worker
```

## Running the tests

```bash
# Unit tests (pure domain logic — no infrastructure needed)
docker compose exec api python -m pytest tests/test_retry_policy.py tests/test_cron.py -v

# Concurrency integration test (proves no double-claims under 8 parallel claimers)
docker compose exec api sh -c \
  'TEST_DATABASE_URL=$DATABASE_URL python -m pytest tests/test_claim_concurrency.py -v'

# Everything (adds: pause/resume + concurrency-cap enforcement under racing
# claimers, cron materialization exactly-once + missed-tick collapse, atomic
# batches, queue-default inheritance, guardrails, worker resurrection)
make test
```

## Configuration

All knobs are environment variables (see [backend/app/core/config.py](backend/app/core/config.py)):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | compose-internal | Postgres DSN (asyncpg) |
| `REDIS_URL` | compose-internal | Redis for wake-ups + stats cache |
| `JWT_SECRET` | dev value | **change in production** |
| `WORKER_CONCURRENCY` | 4 | concurrent jobs per worker process |
| `WORKER_QUEUES` | `default` | comma-separated queues this worker consumes |
| `API_PORT` / `WEB_PORT` | 8000 / 3000 | published host ports (override in `.env` if taken) |
| `LEASE_SECONDS` | 30 | how long a claim is valid without a heartbeat |
| `HEARTBEAT_SECONDS` | 10 | heartbeat/lease-extension interval |
| `POLL_INTERVAL_SECONDS` | 2 | claim poll fallback when Redis is quiet/down |
| `REAPER_INTERVAL_SECONDS` | 5 | lease-expiry sweep interval |
| `SCHEDULER_INTERVAL_SECONDS` | 5 | cron materializer sweep interval |
| `WORKER_SHARD` | unset | if set, this worker claims only queues with that `shard_key` |
| `GEMINI_API_KEY` | empty | enables AI failure summaries on DLQ jobs (off when empty) |
| `WORKER_OFFLINE_AFTER_SECONDS` | 60 | missed-heartbeat threshold for offline |
| `SHUTDOWN_GRACE_SECONDS` | 20 | drain window before releasing in-flight jobs |
| `PRIORITY_AGING_INTERVAL_SECONDS` | 60 | waiting jobs gain +1 effective priority per interval (0 disables) |
| `PRIORITY_AGING_MAX_BOOST` | 200 | cap on the aging boost (bounds starvation at interval × boost) |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | 10 | per-IP login attempts before 429 (fails open if Redis is down) |

Invariant to preserve when tuning: `LEASE_SECONDS ≥ 3 × HEARTBEAT_SECONDS`
(one missed heartbeat must never cause a spurious lease reclaim), and compose
`stop_grace_period` > `SHUTDOWN_GRACE_SECONDS`.

## Repository layout

```
backend/
  app/
    api/            routers + dependencies (HTTP edge)
    services/       business logic (no HTTP, no SQL strings)
    repositories/   data access, guarded UPDATEs, the claim CTE
    domain/         pure decision logic (retry strategies, cron) — unit-testable
    models/         SQLAlchemy 2.0 typed models
    schemas/        Pydantic DTOs
    worker/         worker runtime: registry, tasks, runner, entrypoint
    core/           config, logging, security
  alembic/          migrations (hand-written)
  tests/            unit + concurrency/cron/batch integration tests
frontend/           Next.js 15 dashboard (Tailwind, Recharts)
docker-compose.yml  postgres + redis + api + worker×2 + sharded worker + frontend
```
