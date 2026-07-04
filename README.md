# Chronos — a production-inspired distributed job scheduler

Chronos is a distributed job scheduler built the way real queueing systems
(Temporal, SQS consumers, Sidekiq/Faktory, `pg`-backed queues like Oban) are
built: **PostgreSQL as the transactional queue of record**, lease-based worker
liveness, at-least-once execution with idempotency support, exponential-backoff
retries, and a dead letter queue — behind a clean FastAPI service layer and a
Next.js operations dashboard.

📐 The design rationale and every tradeoff is documented in
[ARCHITECTURE.md](ARCHITECTURE.md), with the one-page decision defense in
[docs/WHY.md](docs/WHY.md). For evaluators:
[docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md) is an eight-section report
(architecture, reliability, concurrency, database, decisions, verification,
scaling limits) and [docs/DIAGRAMS.md](docs/DIAGRAMS.md) holds ten Mermaid
diagrams of the core protocols. Everything combined, print-ready:
[docs/Chronos-Report.pdf](docs/Chronos-Report.pdf) (design doc + measured
benchmarks + rendered diagrams, 21pp).

## Quickstart

```bash
docker compose up --build -d      # postgres, redis, api, 2 workers, frontend
docker compose exec api python -m app.scripts.seed   # demo user + demo jobs
```

| What | Where |
|---|---|
| Dashboard | http://localhost:3000 — login `demo@example.com` / `demo12345` |
| OpenAPI docs (Swagger) | http://localhost:8000/docs |
| API | http://localhost:8000/api/v1 |

If 8000/3000 are taken on your machine, set `API_PORT`/`WEB_PORT` in `.env`
(see `.env.example`) and rerun `docker compose up --build -d`.

The seed enqueues one job of each demo task type, so within a minute you can
watch: an instant success, a 10s job holding a lease, a job that fails twice
and then succeeds (retry pipeline), a flaky job, and a job that exhausts its
budget and lands in the DLQ — requeue it from the dashboard.

## What's demonstrated

| Requirement | Where to look |
|---|---|
| Atomic job claiming | `SELECT … FOR UPDATE SKIP LOCKED` CTE in [backend/app/repositories/jobs.py](backend/app/repositories/jobs.py) (`claim_batch`) |
| Heartbeats | worker loop in [backend/app/worker/runner.py](backend/app/worker/runner.py); one transaction extends worker liveness + all job leases |
| Lease expiration & worker recovery | reaper in [backend/app/services/execution_service.py](backend/app/services/execution_service.py) (`reap_once`), advisory-locked so exactly one sweep runs cluster-wide |
| Retry policies | pure domain logic in [backend/app/domain/retry.py](backend/app/domain/retry.py) — exponential backoff, cap, jitter; per-job policy columns |
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
docker compose exec api python -m pytest tests/test_retry_policy.py -v

# Concurrency integration test (proves no double-claims under 8 parallel claimers)
docker compose exec api sh -c \
  'TEST_DATABASE_URL=$DATABASE_URL python -m pytest tests/test_claim_concurrency.py -v'
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
    domain/         pure decision logic (retry policy) — unit-testable
    models/         SQLAlchemy 2.0 typed models
    schemas/        Pydantic DTOs
    worker/         worker runtime: registry, tasks, runner, entrypoint
    core/           config, logging, security
  alembic/          migrations (hand-written initial schema)
  tests/            unit + concurrency integration tests
frontend/           Next.js 15 dashboard (Tailwind, Recharts)
docker-compose.yml  postgres + redis + api + worker×2 + frontend
```
