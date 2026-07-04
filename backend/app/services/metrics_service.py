"""Prometheus exposition for scheduler health.

Rendered by hand — the metrics are all gauges computed from SQL at scrape
time, so the client library's registry machinery would add a dependency
without adding value. Format: text/plain; version 0.0.4.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttemptStatus
from app.repositories.attempts import AttemptRepository
from app.repositories.jobs import JobRepository
from app.repositories.workers import WorkerRepository


@dataclass(frozen=True, slots=True)
class _Metric:
    name: str
    help_text: str
    samples: list[tuple[str, float]]  # (label_suffix or "", value)


class MetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.jobs = JobRepository(session)
        self.workers = WorkerRepository(session)
        self.attempts = AttemptRepository(session)

    async def render(self) -> str:
        counts = await self.jobs.counts_by_status()
        ready, scheduled = await self.jobs.ready_and_scheduled_counts()
        oldest_ready = await self.jobs.oldest_ready_age_seconds()
        claim_avg, claim_max = await self.jobs.claim_wait_stats(300)
        reclaims_1m = await self.attempts.count_recent_by_status(
            AttemptStatus.LOST, 60
        )
        heartbeat_lag = await self.workers.max_heartbeat_lag_seconds()
        workers_online = await self.workers.count_online()

        metrics = [
            _Metric(
                "chronos_jobs",
                "Jobs by status.",
                [(f'{{status="{s}"}}', float(c)) for s, c in sorted(counts.items())],
            ),
            _Metric(
                "chronos_queue_ready_depth",
                "PENDING jobs due to run now.",
                [("", float(ready))],
            ),
            _Metric(
                "chronos_queue_scheduled_depth",
                "PENDING jobs scheduled for the future (includes retry backoffs).",
                [("", float(scheduled))],
            ),
            _Metric(
                "chronos_oldest_ready_job_age_seconds",
                "Age of the oldest ready-but-unclaimed job. Rising means "
                "workers are dead, saturated, or the queue is backing up.",
                [("", round(oldest_ready, 3))],
            ),
            _Metric(
                "chronos_claim_wait_seconds_avg_5m",
                "Average ready-to-claimed wait for jobs claimed in the last 5m.",
                [("", round(claim_avg, 3))],
            ),
            _Metric(
                "chronos_claim_wait_seconds_max_5m",
                "Worst ready-to-claimed wait for jobs claimed in the last 5m.",
                [("", round(claim_max, 3))],
            ),
            _Metric(
                "chronos_lease_reclaims_1m",
                "Attempts closed as LOST (lease expired) in the last minute.",
                [("", float(reclaims_1m))],
            ),
            _Metric(
                "chronos_worker_heartbeat_lag_seconds",
                "Worst heartbeat staleness among ONLINE workers.",
                [("", round(heartbeat_lag, 3))],
            ),
            _Metric(
                "chronos_workers_online",
                "Workers currently ONLINE.",
                [("", float(workers_online))],
            ),
            _Metric(
                "chronos_dlq_size",
                "Jobs in the dead letter queue (status=dead).",
                [("", float(counts.get("dead", 0)))],
            ),
        ]

        lines: list[str] = []
        for metric in metrics:
            lines.append(f"# HELP {metric.name} {metric.help_text}")
            lines.append(f"# TYPE {metric.name} gauge")
            for labels, value in metric.samples:
                lines.append(f"{metric.name}{labels} {value}")
        return "\n".join(lines) + "\n"
