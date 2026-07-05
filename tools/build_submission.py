"""Build the assignment submission PDF source: docs/Chronos-Submission.html.

One self-contained document: cover + links, how to run, requirements
coverage, live dashboard screenshots (embedded), an API reference generated
from the running service's OpenAPI spec, then the full design document,
benchmarks and protocol diagrams (incl. the ER diagram).

    python tools/build_submission.py
    msedge --headless --print-to-pdf=docs/Chronos-Submission.pdf \
           --virtual-time-budget=30000 --no-pdf-header-footer \
           docs/Chronos-Submission.html

Fill in the hosted/repo URLs below (or leave blank to print the
run-locally instructions only) and rebuild — takes seconds.
"""

import base64
import json
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Submission metadata — edit these, then rebuild.
AUTHOR = "Gokul Shiva"
EMAIL = "gokulshiva085@gmail.com"
REPO_URL = "https://github.com/Shiva085000/chronos-scheduler"
FRONTEND_URL = "https://frontend-production-e4f9.up.railway.app"
BACKEND_URL = "https://api-production-f587.up.railway.app"
# The running local stack the API reference is generated from:
LOCAL_API = "http://localhost:8010"
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"

from build_report import CSS, MERMAID, convert  # noqa: E402 — same directory

EXTRA_CSS = """
.cover .meta { margin-top: 18pt; font-size: 11pt; color: #333; }
.cover .links { margin-top: 10pt; font-size: 10pt; }
.cover .links code { font-size: 9.5pt; }
.shot { page-break-inside: avoid; margin: 14pt 0; text-align: center; }
.shot img { width: 100%; max-width: 172mm; border: 1px solid #ccc;
            border-radius: 4px; }
.shot figcaption { font-size: 9pt; color: #555; margin-top: 4pt; }
.badge { display: inline-block; background: #eef2ee; border: 1px solid #cfd8cf;
         border-radius: 3px; padding: 0 5px; font-size: 8.5pt; }
"""

SCREENSHOTS = [
    ("01-dashboard.png", "Dashboard — cluster stats, throughput (succeeded/failed per minute), status distribution; polls live."),
    ("02-queues.png", "Queues — per-queue counts, pause/resume, shard badges, concurrency caps, default retry policy, config dialog."),
    ("03-jobs.png", "Job explorer — status/queue filters, pagination, priorities, attempts."),
    ("04-job-detail.png", "Job detail — a DLQ'd job on the hosted deployment: payload, full traceback, per-attempt execution history with worker assignment, one-click requeue, and the AI Failure Analysis note produced live by the multi-agent LangGraph pipeline (triage classified it [permanent], diagnosed the invalid signature, recommended the fix)."),
    ("05-schedules.png", "Recurring schedules — cron expression, next/last run cursors, pause/resume, create dialog with presets."),
    ("06-dlq.png", "Dead Letter Queue — jobs that exhausted their retry budget; inspect and requeue with a fresh budget."),
    ("07-workers.png", "Worker fleet — liveness via heartbeats; a worker silent for 60s is declared offline and its leases reclaimed."),
    ("08-login.png", "Login — JWT auth, registration, and one-click demo login."),
]

INTRO_MD = f"""
# Submission overview

**Chronos** is a production-inspired distributed job scheduling platform:
an authenticated REST API accepts immediate, delayed, recurring (cron),
batch and dependency-chained jobs into configurable queues
(organization → project → queue), and a horizontally scalable worker fleet
executes them with explicit reliability semantics — atomic
`FOR UPDATE SKIP LOCKED` claiming, lease-based failure detection with
heartbeats, fixed/linear/exponential retries with jitter, a dead letter
queue, idempotent enqueue, and graceful drain on shutdown. PostgreSQL is
the single source of truth; Redis only shaves claim latency (advisory wake
channel) and dashboard load.

**Highlights an evaluator may want first:**

- **Reliability, verified:** `SIGKILL` a worker mid-job and the job is
  re-queued by the reaper within ~35 s with the attempt recorded as *lost*
  (§12 of the design document reproduces this and five other failure
  drills). 39 automated tests include claim-atomicity under 8 racing
  claimers and exact concurrency-cap enforcement.
- **Measured performance:** 309 → 1,505 jobs/s scaling 1 → 8 workers,
  35 ms p50 dispatch latency (benchmarks section).
- **Every decision defended:** the design document explains each tradeoff
  (why Postgres over a broker, why leases over liveness pings, why
  at-least-once, why the DLQ is a status and not a table…).
- **Bonus features implemented:** workflow dependencies, queue sharding,
  RBAC (owner/admin/member/viewer), AI failure analysis via a multi-agent
  LangGraph pipeline, distributed locking (advisory locks), event-driven
  execution (wake channel), and login rate limiting.

## Live demo & source

| What | Where |
|---|---|
| **Live dashboard** | {FRONTEND_URL} — click **Demo Login** (`demo@example.com` / `demo12345`) |
| **Live API (Swagger)** | {BACKEND_URL}/docs |
| **Source code** | {REPO_URL} |
| Read-only RBAC user | `viewer@example.com` / `viewer12345` — mutating actions return 403 |
| Prometheus metrics | {BACKEND_URL}/metrics |

The hosted stack runs on Railway: managed Postgres + Redis, the API, and a
worker service (private networking between them; the workers claim from
all demo queues). The dedicated-shard worker topology is exercised in the
local compose stack and the automated tests.

## Running locally

```bash
git clone {REPO_URL}
cd chronos-scheduler
docker compose up --build -d      # postgres, redis, api, 2 workers, 1 sharded worker, frontend
docker compose exec api python -m app.scripts.seed   # demo data
```

Dashboard at http://localhost:3000, Swagger at http://localhost:8000/docs.
Full test suite: `make test` (39 tests, unit + DB integration).

Within ~2 minutes of seeding, the dashboard shows: an instant success, a
job holding a lease, the retry pipeline succeeding on attempt 3, a DLQ
entry, a capped queue executing an atomic batch strictly one-at-a-time, an
A → B → C workflow unlocking in order, a cron schedule firing every other
minute, and a sharded queue served only by the shard worker.

## Requirements coverage

### Core requirements

| Requirement | Status | Evidence |
|---|---|---|
| Authentication & project management; projects own queues | ✅ | JWT auth; org → project → queue chain provisioned at registration; `/projects`, `/queues` APIs |
| Queue configuration: priority, concurrency limits, retry policy, pause/resume, statistics | ✅ | queue rows carry all controls; caps enforced **atomically** under the queue row lock; per-queue stats endpoint + UI |
| Immediate, delayed, scheduled, recurring (cron), batch jobs via REST | ✅ | `run_at` for delayed/scheduled; `schedules` + advisory-locked materializer for cron (exactly-once per tick, missed ticks collapse); `POST /jobs/batch` all-or-nothing |
| Worker service: polls, atomic claims, concurrent execution, heartbeats, graceful shutdown | ✅ | asyncio worker; `SKIP LOCKED` claim CTE; one-transaction heartbeat extends liveness + all leases; SIGTERM drain releases unfinished jobs with the attempt refunded |
| Full lifecycle with retries and DLQ | ✅ | PENDING → RUNNING → SUCCEEDED / retry / DEAD; every transition a guarded UPDATE; DLQ requeue from UI/API |
| Retry strategies: fixed, linear, exponential | ✅ | per-job `backoff_strategy` + base/factor/cap/jitter, inheritable from queue defaults |
| Execution logs, retry history, worker assignment, timestamps, metrics | ✅ | append-only `job_attempts` audit trail; structured JSON logs with correlation ids; Prometheus metrics |
| Dashboard: queues, jobs, workers, retry failed, throughput & health | ✅ | screenshots below; 4 s polling for live updates |

### Bonus features

| Bonus | Status | Notes |
|---|---|---|
| Workflow dependencies | ✅ | `job_dependencies`; claim scan admits a job only after all prerequisites SUCCEED |
| Rate limiting | ✅ (login) | per-IP login throttle (Redis fixed window, fails open) |
| Distributed locking | ✅ | Postgres advisory locks coordinate the reaper and cron materializer cluster-wide |
| Queue sharding | ✅ | queues carry `shard_key`; a worker started with `WORKER_SHARD` claims only its shard (dedicated compose node) |
| Event-driven execution | ✅ | Redis pub/sub wake channel; poll fallback keeps correctness when Redis is down |
| WebSocket live updates | ➖ | polling chosen (explicitly permitted); tradeoff documented |
| Role-based access control | ✅ | owner > admin > member > viewer, enforced per endpoint |
| AI-generated failure summaries | ✅ | multi-agent **LangGraph** pipeline (triage → diagnose → remediate → compose, conditional routing: transient failures skip deep diagnosis) over Gemini; cached by error hash; degrades gracefully without an API key |

### Deliverables

| Deliverable | Where |
|---|---|
| Source code + setup | repository, `README.md` (one-command compose) |
| Architecture diagram | Protocol Diagrams §1 (this document) |
| ER diagram | Protocol Diagrams §11 (this document) |
| API documentation | generated reference below + live Swagger at `/docs` |
| Design decisions document | full engineering design document included below |
| Automated tests | 39 tests: retry/cron domain units, claim atomicity, cap enforcement, batches, schedule materialization, registration, guardrails, worker resurrection |
"""


def links_block() -> str:
    rows = []
    if FRONTEND_URL:
        rows.append(f"<p>Live dashboard: <code>{FRONTEND_URL}</code></p>")
    if BACKEND_URL:
        rows.append(
            f"<p>Live API (Swagger): <code>{BACKEND_URL}/docs</code></p>"
        )
    if REPO_URL:
        rows.append(f"<p>Source code: <code>{REPO_URL}</code></p>")
    if not rows:
        rows.append(
            "<p>Runs locally in one command — see <em>How to run</em>.</p>"
        )
    return "\n".join(rows)


def screenshots_html() -> str:
    parts = ["<h1>Dashboard walkthrough</h1>",
             "<p>Captured from the running system with seeded demo data.</p>"]
    for name, caption in SCREENSHOTS:
        path = SHOTS / name
        if not path.exists():
            continue
        b64 = base64.b64encode(path.read_bytes()).decode()
        parts.append(
            f"<figure class='shot'><img src='data:image/png;base64,{b64}'/>"
            f"<figcaption>{caption}</figcaption></figure>"
        )
    return "\n".join(parts)


def api_reference_md() -> str:
    with urllib.request.urlopen(f"{LOCAL_API}/openapi.json") as r:
        spec = json.load(r)
    by_tag: dict[str, list[tuple[str, str, str, bool]]] = {}
    for path, methods in sorted(spec["paths"].items()):
        for method, op in methods.items():
            tag = (op.get("tags") or ["misc"])[0]
            secured = bool(op.get("security"))
            by_tag.setdefault(tag, []).append(
                (method.upper(), path, op.get("summary", ""), secured)
            )
    lines = [
        "# API reference",
        "",
        "Generated from the service's OpenAPI spec; the interactive version "
        "(request/response schemas, try-it-out) is served at `/docs`. "
        "All endpoints return structured errors, and list endpoints support "
        "`limit`/`offset` pagination plus field filters. 🔒 = bearer JWT "
        "required.",
        "",
    ]
    for tag, ops in by_tag.items():
        lines += [f"## {tag}", "", "| Method | Path | Summary | |",
                  "|---|---|---|---|"]
        for method, path, summary, secured in ops:
            lock = "🔒" if secured else ""
            lines.append(f"| `{method}` | `{path}` | {summary} | {lock} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    sections = [
        convert(INTRO_MD),
        screenshots_html(),
        convert(api_reference_md()),
        convert((DOCS / "DESIGN_DOC.md").read_text(encoding="utf-8")),
        convert((DOCS / "BENCHMARKS.md").read_text(encoding="utf-8")),
        convert((DOCS / "DIAGRAMS.md").read_text(encoding="utf-8")),
    ]
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Chronos — Distributed Job Scheduler — Submission</title>",
        f"<style>{CSS}{EXTRA_CSS}</style></head><body>",
        "<div class='cover'><h1>Chronos</h1>",
        "<p>A production-inspired distributed job scheduler</p>",
        "<p><strong>Intern Assignment Submission</strong></p>",
        f"<div class='meta'><p>{AUTHOR}</p><p>{EMAIL}</p></div>",
        f"<div class='links'>{links_block()}</div>",
        "<p style='margin-top:14pt;color:#777;font-size:9pt'>"
        "Contents: overview &amp; requirements coverage · dashboard "
        "screenshots · API reference · engineering design document · "
        "measured benchmarks · protocol &amp; ER diagrams</p></div>",
    ]
    for html in sections:
        parts.append(f"<section class='part'>{html}</section>")
    parts.append(MERMAID)
    parts.append("</body></html>")

    out = DOCS / "Chronos-Submission.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
