"""Job data access.

Every state transition here is a *guarded* UPDATE — the WHERE clause
restates the expected current state (status, lease holder), so a stale
actor (a worker whose lease was reclaimed, a concurrent cancel) gets
rowcount 0 instead of silently clobbering someone else's transition.
This is the fencing mechanism for the whole system.

All time arithmetic uses the database clock (now()), never worker
clocks, so clock skew between processes cannot corrupt lease decisions.
"""

import uuid
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus


def _db_now_plus(seconds: float):
    """now() + interval, computed on the DB server. `seconds` is validated
    numeric input from our own config/domain layer, never user input."""
    return text(f"now() + interval '{float(seconds)} seconds'")


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # enqueue / read
    # ------------------------------------------------------------------

    def add(self, job: Job) -> Job:
        self.session.add(job)
        return job

    async def get(self, job_id: uuid.UUID) -> Job | None:
        return await self.session.get(Job, job_id)

    async def get_for_owner(
        self, job_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Job | None:
        result = await self.session.execute(
            select(Job).where(Job.id == job_id, Job.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, owner_id: uuid.UUID, key: str
    ) -> Job | None:
        result = await self.session.execute(
            select(Job).where(Job.owner_id == owner_id, Job.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: JobStatus | None = None,
        queue: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        conditions = [Job.owner_id == owner_id]
        if status is not None:
            conditions.append(Job.status == status)
        if queue is not None:
            conditions.append(Job.queue == queue)

        total = (
            await self.session.execute(
                select(func.count()).select_from(Job).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(Job)
                .where(*conditions)
                .order_by(Job.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return list(rows), total

    # ------------------------------------------------------------------
    # atomic claim (the hot path)
    # ------------------------------------------------------------------

    async def claim_batch(
        self,
        worker_id: uuid.UUID,
        *,
        queues: list[str],
        limit: int,
        lease_seconds: int,
        aging_interval_seconds: int = 60,
        aging_max_boost: int = 200,
    ) -> list[Job]:
        """Atomically claim up to `limit` ready jobs from `queues`.

        WITH candidates AS (
            SELECT id FROM jobs
            WHERE status = 'pending' AND run_at <= now() AND queue = ANY(...)
            ORDER BY effective_priority DESC, run_at, created_at
            LIMIT :n
            FOR UPDATE SKIP LOCKED
        )
        UPDATE jobs SET status='running', locked_by=..., ... FROM candidates ...
        RETURNING jobs.*

        SKIP LOCKED makes concurrent claimers pass over rows another
        transaction has already locked instead of blocking or double-
        claiming — each ready job is handed to exactly one worker.

        Starvation bound: effective priority = priority + one point per
        `aging_interval_seconds` waited since becoming ready, capped at
        `aging_max_boost`. With the defaults (60s, 200) the lowest-priority
        job (-100) outranks a sustained stream of +100 jobs after at most
        200 minutes. The `run_at <= now()` predicate keeps the wait
        non-negative. Cost: the ready set is sorted by a computed key, so
        the claim index filters but no longer pre-sorts — acceptable while
        the ready set is small (it is, by design: the partial index tracks
        backlog, not history). Set aging_interval_seconds=0 to restore
        strict priority order.
        """
        if aging_interval_seconds > 0:
            age_boost = func.least(
                func.floor(
                    func.extract("epoch", func.now() - Job.run_at)
                    / aging_interval_seconds
                ),
                aging_max_boost,
            )
            priority_key = (Job.priority + age_boost).desc()
        else:
            priority_key = Job.priority.desc()

        candidates = (
            select(Job.id)
            .where(
                Job.status == JobStatus.PENDING,
                Job.run_at <= func.now(),
                Job.queue.in_(queues),
            )
            .order_by(priority_key, Job.run_at.asc(), Job.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("candidates")
        )
        stmt = (
            update(Job)
            .where(Job.id == candidates.c.id)
            .values(
                status=JobStatus.RUNNING,
                locked_by=worker_id,
                attempt_count=Job.attempt_count + 1,
                started_at=func.now(),
                lease_expires_at=_db_now_plus(lease_seconds),
                updated_at=func.now(),
            )
            .returning(Job)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # lease maintenance
    # ------------------------------------------------------------------

    async def extend_leases(self, worker_id: uuid.UUID, lease_seconds: int) -> int:
        """Heartbeat: push out the lease on every job this worker still owns."""
        result = await self.session.execute(
            update(Job)
            .where(Job.locked_by == worker_id, Job.status == JobStatus.RUNNING)
            .values(lease_expires_at=_db_now_plus(lease_seconds), updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0

    async def select_expired_for_update(self, limit: int = 100) -> list[Job]:
        """Lock a batch of lease-expired RUNNING jobs for the reaper.

        SKIP LOCKED means a job currently being finalized by its (still
        alive) worker is skipped rather than fought over.
        """
        result = await self.session.execute(
            select(Job)
            .where(Job.status == JobStatus.RUNNING, Job.lease_expires_at < func.now())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # guarded transitions out of RUNNING (fenced by locked_by)
    # ------------------------------------------------------------------

    async def finish_success(
        self, job_id: uuid.UUID, worker_id: uuid.UUID, result_payload: dict[str, Any] | None
    ) -> Job | None:
        """RUNNING -> SUCCEEDED. Returns None if the lease was lost."""
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING,
                Job.locked_by == worker_id,
            )
            .values(
                status=JobStatus.SUCCEEDED,
                result=result_payload,
                finished_at=func.now(),
                locked_by=None,
                lease_expires_at=None,
                last_error=None,
                updated_at=func.now(),
            )
            .returning(Job)
            .execution_options(synchronize_session=False)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def finish_failure(
        self,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        *,
        error: str,
        retry: bool,
        retry_delay_seconds: float,
    ) -> Job | None:
        """RUNNING -> PENDING (retry after backoff) or DEAD (to the DLQ)."""
        values: dict[str, Any] = {
            "locked_by": None,
            "lease_expires_at": None,
            "last_error": error,
            "updated_at": func.now(),
        }
        if retry:
            values["status"] = JobStatus.PENDING
            values["run_at"] = _db_now_plus(retry_delay_seconds)
        else:
            values["status"] = JobStatus.DEAD
            values["finished_at"] = func.now()

        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING,
                Job.locked_by == worker_id,
            )
            .values(**values)
            .returning(Job)
            .execution_options(synchronize_session=False)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def release(self, job_id: uuid.UUID, worker_id: uuid.UUID) -> Job | None:
        """RUNNING -> PENDING immediately, refunding the attempt.

        Used on graceful shutdown for jobs we abort mid-run: the failure
        wasn't the job's fault, so it should not burn an attempt or wait
        out a backoff.
        """
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING,
                Job.locked_by == worker_id,
            )
            .values(
                status=JobStatus.PENDING,
                attempt_count=Job.attempt_count - 1,
                run_at=func.now(),
                locked_by=None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
            .returning(Job)
            .execution_options(synchronize_session=False)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # user-facing transitions
    # ------------------------------------------------------------------

    async def cancel(self, job_id: uuid.UUID, owner_id: uuid.UUID) -> Job | None:
        """PENDING -> CANCELLED. Running jobs cannot be cancelled (see docs)."""
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.owner_id == owner_id,
                Job.status == JobStatus.PENDING,
            )
            .values(
                status=JobStatus.CANCELLED,
                finished_at=func.now(),
                updated_at=func.now(),
            )
            .returning(Job)
            .execution_options(synchronize_session=False)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def requeue(self, job_id: uuid.UUID, owner_id: uuid.UUID) -> Job | None:
        """DEAD/CANCELLED -> PENDING with a fresh attempt budget."""
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.owner_id == owner_id,
                Job.status.in_([JobStatus.DEAD, JobStatus.CANCELLED]),
            )
            .values(
                status=JobStatus.PENDING,
                attempt_count=0,
                run_at=func.now(),
                finished_at=None,
                result=None,
                locked_by=None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
            .returning(Job)
            .execution_options(synchronize_session=False)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------

    async def counts_by_status(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(Job.status, func.count()).group_by(Job.status)
        )
        counts = {status.value: 0 for status in JobStatus}
        for status, count in rows:
            counts[status.value] = count
        return counts

    async def oldest_ready_age_seconds(self) -> float:
        """Age of the oldest job that is ready to run but unclaimed — the
        single best queue-health signal: it rises when workers are dead,
        saturated, or the queue is backing up, and is ~0 when healthy."""
        result = await self.session.execute(
            select(
                func.extract("epoch", func.now() - func.min(Job.run_at))
            ).where(Job.status == JobStatus.PENDING, Job.run_at <= func.now())
        )
        value = result.scalar()
        return float(value) if value is not None else 0.0

    async def claim_wait_stats(
        self, window_seconds: int = 300
    ) -> tuple[float, float]:
        """(avg, max) seconds jobs claimed in the window waited between
        becoming ready (run_at) and being claimed (started_at)."""
        wait = func.extract("epoch", Job.started_at - Job.run_at)
        result = await self.session.execute(
            select(func.avg(wait), func.max(wait)).where(
                Job.started_at.is_not(None),
                Job.started_at
                >= text(f"now() - interval '{int(window_seconds)} seconds'"),
            )
        )
        avg_wait, max_wait = result.one()
        return (
            float(avg_wait) if avg_wait is not None else 0.0,
            float(max_wait) if max_wait is not None else 0.0,
        )

    async def ready_and_scheduled_counts(self) -> tuple[int, int]:
        row = (
            await self.session.execute(
                select(
                    func.count().filter(Job.run_at <= func.now()),
                    func.count().filter(Job.run_at > func.now()),
                ).where(Job.status == JobStatus.PENDING)
            )
        ).one()
        return int(row[0]), int(row[1])
