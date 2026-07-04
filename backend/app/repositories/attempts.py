import datetime as dt
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttemptStatus, JobAttempt


class AttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def start(
        self, job_id: uuid.UUID, worker_id: uuid.UUID, attempt_number: int
    ) -> JobAttempt:
        attempt = JobAttempt(
            job_id=job_id,
            worker_id=worker_id,
            attempt_number=attempt_number,
            status=AttemptStatus.RUNNING,
        )
        self.session.add(attempt)
        return attempt

    async def finish(
        self,
        job_id: uuid.UUID,
        attempt_number: int,
        status: AttemptStatus,
        error: str | None = None,
    ) -> int:
        """Close the in-flight attempt row. Guarded by status = RUNNING so
        terminal attempt rows (including ones from a previous requeue
        epoch) are never rewritten."""
        result = await self.session.execute(
            update(JobAttempt)
            .where(
                JobAttempt.job_id == job_id,
                JobAttempt.attempt_number == attempt_number,
                JobAttempt.status == AttemptStatus.RUNNING,
            )
            .values(status=status, error=error, finished_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0

    async def list_for_job(self, job_id: uuid.UUID) -> list[JobAttempt]:
        result = await self.session.execute(
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id)
            .order_by(JobAttempt.started_at.asc())
        )
        return list(result.scalars().all())

    async def count_recent_by_status(
        self, status: AttemptStatus, window_seconds: int
    ) -> int:
        from sqlalchemy import text

        result = await self.session.execute(
            select(func.count()).where(
                JobAttempt.status == status,
                JobAttempt.finished_at
                >= text(f"now() - interval '{int(window_seconds)} seconds'"),
            )
        )
        return result.scalar_one()

    async def throughput_by_minute(
        self, since: dt.datetime
    ) -> list[tuple[dt.datetime, AttemptStatus, int]]:
        minute = func.date_trunc("minute", JobAttempt.finished_at)
        result = await self.session.execute(
            select(minute, JobAttempt.status, func.count())
            .where(
                JobAttempt.finished_at.is_not(None),
                JobAttempt.finished_at >= since,
                JobAttempt.status.in_(
                    [AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.LOST]
                ),
            )
            .group_by(minute, JobAttempt.status)
        )
        return [(m, s, c) for m, s, c in result.all()]
