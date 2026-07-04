import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.events import EventBus
from app.models import AttemptStatus, JobStatus
from app.repositories.attempts import AttemptRepository
from app.repositories.jobs import JobRepository
from app.repositories.workers import WorkerRepository
from app.schemas.stats import StatsOverview, ThroughputPoint

STATS_CACHE_KEY = "chronos:stats:overview"


class StatsService:
    def __init__(self, session: AsyncSession, bus: EventBus) -> None:
        self.session = session
        self.bus = bus
        self.jobs = JobRepository(session)
        self.workers = WorkerRepository(session)
        self.attempts = AttemptRepository(session)

    async def overview(self) -> StatsOverview:
        """Cluster-wide dashboard stats, cached briefly in Redis so a wall
        of dashboards polling every few seconds doesn't hammer Postgres."""
        cached = await self.bus.get_cached(STATS_CACHE_KEY)
        if cached:
            return StatsOverview.model_validate_json(cached)

        overview = await self._compute()
        await self.bus.set_cached(
            STATS_CACHE_KEY,
            overview.model_dump_json(),
            settings.stats_cache_ttl_seconds,
        )
        return overview

    async def _compute(self) -> StatsOverview:
        now = dt.datetime.now(dt.timezone.utc)
        counts = await self.jobs.counts_by_status()
        ready_now, scheduled_later = await self.jobs.ready_and_scheduled_counts()
        workers_online = await self.workers.count_online()

        since = now - dt.timedelta(hours=1)
        raw = await self.attempts.throughput_by_minute(since)

        buckets: dict[dt.datetime, dict[str, int]] = {}
        for i in range(61):
            minute = (since + dt.timedelta(minutes=i)).replace(
                second=0, microsecond=0
            )
            buckets[minute] = {"succeeded": 0, "failed": 0}
        for minute, status, count in raw:
            key = minute.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
            if key not in buckets:
                continue
            if status == AttemptStatus.SUCCEEDED:
                buckets[key]["succeeded"] += count
            else:  # FAILED and LOST both count as failures for the chart
                buckets[key]["failed"] += count

        throughput = [
            ThroughputPoint(minute=m, succeeded=v["succeeded"], failed=v["failed"])
            for m, v in sorted(buckets.items())
        ]
        return StatsOverview(
            counts_by_status=counts,
            ready_now=ready_now,
            scheduled_later=scheduled_later,
            workers_online=workers_online,
            dlq_size=counts.get(JobStatus.DEAD.value, 0),
            throughput_last_hour=throughput,
            generated_at=now,
        )
