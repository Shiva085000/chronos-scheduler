# Chronos — Diagrams

Mermaid sources. Render on GitHub, or export to PDF with
`mmdc -i DIAGRAMS.md -o diagrams.pdf` (mermaid-cli).

## 1. System architecture

```mermaid
flowchart LR
  subgraph Clients
    UI[Next.js dashboard]
    CLI[API clients]
  end

  subgraph Control["API process"]
    API[FastAPI<br/>routers → services → repositories]
  end

  subgraph Data
    PG[(PostgreSQL<br/>jobs · attempts · workers · users)]
    RD[(Redis<br/>wake channel · stats cache)]
  end

  subgraph Fleet["Worker fleet ×N"]
    W1[worker<br/>consume · heartbeat · reaper]
    W2[worker<br/>consume · heartbeat · reaper]
  end

  UI -->|HTTPS + JWT| API
  CLI -->|HTTPS + JWT| API
  API -->|transactions| PG
  API -.->|publish wake, best effort| RD
  RD -.->|subscribe| W1 & W2
  W1 & W2 -->|"claim (SKIP LOCKED), heartbeat, finalize"| PG
```

## 2. Job lifecycle

```mermaid
stateDiagram-v2
  [*] --> PENDING: enqueue
  PENDING --> RUNNING: atomic claim
  PENDING --> CANCELLED: cancel (owner)
  RUNNING --> SUCCEEDED: handler ok
  RUNNING --> PENDING: fail, budget left<br/>run_at = now + backoff
  RUNNING --> PENDING: shutdown release<br/>attempt refunded
  RUNNING --> DEAD: budget exhausted
  DEAD --> PENDING: DLQ requeue<br/>fresh budget
  CANCELLED --> PENDING: requeue
  SUCCEEDED --> [*]
  note right of DEAD: DEAD is the dead letter queue
```

## 3. Worker lifecycle

```mermaid
stateDiagram-v2
  [*] --> ONLINE: register, start loops
  ONLINE --> ONLINE: heartbeat / 10s<br/>extends own leases
  ONLINE --> DRAINING: SIGTERM
  DRAINING --> OFFLINE: drained or grace elapsed<br/>remaining jobs released
  ONLINE --> OFFLINE: crash — reaper marks offline<br/>after 60s without heartbeat
  OFFLINE --> [*]
```

## 4. Claim sequence (two workers, no double-claim)

```mermaid
sequenceDiagram
  participant W1 as Worker 1
  participant W2 as Worker 2
  participant PG as PostgreSQL

  par concurrent claims
    W1->>PG: WITH c AS (SELECT ... FOR UPDATE SKIP LOCKED) UPDATE ... RETURNING
    W2->>PG: same statement
  end
  PG-->>W1: jobs A, B (rows locked first)
  PG-->>W2: job C (A, B skipped, not blocked)
  Note over PG: same tx: INSERT job_attempts row per claim
  W1->>W1: execute A, B
  W2->>W2: execute C
```

## 5. Lease expiry recovery

```mermaid
sequenceDiagram
  participant W as Worker (dead)
  participant PG as PostgreSQL
  participant R as Reaper (any worker)
  participant W2 as Worker 2

  W->>PG: claim job, lease = now()+30s
  Note over W: crash — heartbeats stop
  Note over PG: t+30s: lease expired
  R->>PG: SELECT expired FOR UPDATE SKIP LOCKED
  R->>PG: attempt := LOST, decide retry vs DLQ
  R->>PG: job := PENDING, run_at = now()+backoff
  W2->>PG: next claim picks the job up
  W2->>PG: SUCCEEDED (attempt n+1)
```

## 6. Retry flow

```mermaid
flowchart TD
  F[attempt n failed<br/>handler error, timeout, or lease lost] --> D{n < max_attempts?}
  D -- yes --> B["delay = min(base × factor^(n−1), cap) + jitter ≤ 20%"]
  B --> P[status = PENDING<br/>run_at = now + delay]
  P --> C[claimed when run_at ≤ now<br/>the claim predicate is the scheduler]
  C --> F2{outcome}
  F2 -- success --> S[SUCCEEDED]
  F2 -- failure --> F
  D -- no --> X[status = DEAD → DLQ<br/>manual requeue resets budget]
```

## 7. Graceful shutdown

```mermaid
sequenceDiagram
  participant D as Docker (SIGTERM)
  participant W as Worker
  participant PG as PostgreSQL

  D->>W: SIGTERM
  W->>W: stop claiming
  W->>PG: worker.status = DRAINING
  Note over W: wait ≤ 20s grace<br/>heartbeats keep leases alive
  alt job finishes in time
    W->>PG: job := SUCCEEDED
  else grace elapsed
    W->>W: cancel task
    W->>PG: job := PENDING, run_at = now()<br/>attempt_count −1, attempt := ABORTED
  end
  W->>PG: worker := OFFLINE, stopped_at
  W-->>D: exit before stop_grace_period (30s)
```

## 8. Idempotency flow

```mermaid
sequenceDiagram
  participant C as Client
  participant API as API
  participant PG as PostgreSQL

  C->>API: POST /jobs, Idempotency-Key K
  API->>PG: INSERT job (owner, K)
  PG-->>API: ok — unique partial index admits one
  API-->>C: 201 Created, job J

  C->>API: retry same request, key K
  API->>PG: INSERT job (owner, K)
  PG-->>API: unique_violation
  API->>PG: SELECT job WHERE owner, K
  API-->>C: 200 OK, same job J
```

## 9. Reaper coordination

```mermaid
sequenceDiagram
  participant R1 as Reaper in worker 1
  participant R2 as Reaper in worker 2
  participant PG as PostgreSQL

  par every 5s tick
    R1->>PG: pg_try_advisory_xact_lock(K)
    R2->>PG: pg_try_advisory_xact_lock(K)
  end
  PG-->>R1: true — sweep runs
  PG-->>R2: false — skip this tick
  R1->>PG: reclaim expired leases (≤100)
  R1->>PG: mark stale workers OFFLINE
  R1->>PG: COMMIT — lock released with tx
  Note over R1,R2: crash mid-sweep = rollback,<br/>lock freed, next tick retries
```

## 10. Worker crash recovery (fencing)

```mermaid
sequenceDiagram
  participant W as Worker (paused / zombie)
  participant PG as PostgreSQL
  participant R as Reaper

  W->>PG: claim job, locked_by = W
  Note over W: 40s stall — no heartbeats
  R->>PG: lease expired → attempt LOST,<br/>job PENDING, locked_by = NULL
  Note over W: resumes, handler completes
  W->>PG: UPDATE ... WHERE status='running'<br/>AND locked_by = W
  PG-->>W: 0 rows — lease lost
  W->>W: log lease_lost, discard result
  Note over PG: reaper's verdict stands.<br/>State consistent; side effects may<br/>duplicate — at-least-once by design
```
