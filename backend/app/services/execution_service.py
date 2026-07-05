"""Execution-side operations used by worker processes and the reaper.

Unlike the request-scoped API services, this service owns a session
factory and opens a short transaction per operation — a worker must not
pin a DB connection for the lifetime of a (potentially long) job.
"""

import datetime as dt
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.domain.cron import next_run_after
from app.domain.retry import RetryPolicy, decide_failure
from app.events import EventBus
from app.models import AttemptStatus, Job, JobStatus, Worker, WorkerStatus
from app.repositories.attempts import AttemptRepository
from app.repositories.jobs import JobRepository
from app.repositories.schedules import ScheduleRepository
from app.repositories.workers import WorkerRepository
from app.services.ai_summary_service import AISummaryService

logger = structlog.get_logger(__name__)

# Single well-known key: only one reaper sweep runs cluster-wide at a time.
REAPER_ADVISORY_LOCK_KEY = 0x5EA9E4
# Same trick for the schedule materializer.
SCHEDULER_ADVISORY_LOCK_KEY = 0x5C4ED0


def _policy_of(job: Job) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=job.max_attempts,
        backoff_base_seconds=job.backoff_base_seconds,
        backoff_factor=job.backoff_factor,
        backoff_max_seconds=job.backoff_max_seconds,
        strategy=job.backoff_strategy,
    )


class ExecutionService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        bus: EventBus | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.bus = bus

    # ------------------------------------------------------------------
    # worker registration / heartbeat
    # ------------------------------------------------------------------

    async def register_worker(
        self, worker_id: uuid.UUID, name: str, concurrency: int
    ) -> None:
        async with self.session_factory() as session:
            WorkerRepository(session).add(
                Worker(
                    id=worker_id,
                    name=name,
                    status=WorkerStatus.ONLINE,
                    concurrency=concurrency,
                )
            )
            await session.commit()

    async def heartbeat(self, worker_id: uuid.UUID, lease_seconds: int) -> int:
        """Refresh worker liveness and extend leases on all held jobs.

        One transaction on purpose: either both liveness and leases move
        forward together, or neither does.
        """
        async with self.session_factory() as session:
            resurrected = await WorkerRepository(session).heartbeat(worker_id)
            extended = await JobRepository(session).extend_leases(
                worker_id, lease_seconds
            )
            await session.commit()
            if resurrected:
                logger.warning(
                    "worker.resurrected_after_false_death",
                    worker_id=str(worker_id),
                )
            return extended

    async def set_worker_status(
        self, worker_id: uuid.UUID, status: WorkerStatus, *, stopped: bool = False
    ) -> None:
        async with self.session_factory() as session:
            await WorkerRepository(session).set_status(
                worker_id, status, stopped=stopped
            )
            await session.commit()

    # ------------------------------------------------------------------
    # claim / finish
    # ------------------------------------------------------------------

    async def claim_jobs(
        self,
        worker_id: uuid.UUID,
        *,
        queues: list[str],
        limit: int,
        lease_seconds: int,
        shard_key: int | None = None,
    ) -> list[Job]:
        """Atomically claim ready jobs and open their attempt records in
        the same transaction — a claim without an audit row cannot exist."""
        async with self.session_factory() as session:
            jobs = await JobRepository(session).claim_batch(
                worker_id,
                queues=queues,
                limit=limit,
                lease_seconds=lease_seconds,
                aging_interval_seconds=settings.priority_aging_interval_seconds,
                aging_max_boost=settings.priority_aging_max_boost,
                shard_key=shard_key,
            )
            attempt_repo = AttemptRepository(session)
            for job in jobs:
                attempt_repo.start(job.id, worker_id, job.attempt_count)
            await session.commit()
            return jobs

    async def complete_success(
        self, job: Job, worker_id: uuid.UUID, result: dict[str, Any] | None
    ) -> bool:
        async with self.session_factory() as session:
            updated = await JobRepository(session).finish_success(
                job.id, worker_id, result
            )
            if updated is None:
                # Lease was reclaimed while we ran; the reaper already
                # closed the attempt as LOST. Do not overwrite its verdict.
                await session.rollback()
                logger.warning(
                    "execution.lease_lost_on_success",
                    job_id=str(job.id),
                    worker_id=str(worker_id),
                )
                return False
            await AttemptRepository(session).finish(
                job.id, job.attempt_count, AttemptStatus.SUCCEEDED
            )
            await session.commit()
            return True

    async def complete_failure(
        self, job: Job, worker_id: uuid.UUID, error: str
    ) -> bool:
        """Record a failed attempt and either schedule a retry or move the
        job to the DLQ, per its retry policy."""
        decision = decide_failure(_policy_of(job), job.attempt_count)
        async with self.session_factory() as session:
            updated = await JobRepository(session).finish_failure(
                job.id,
                worker_id,
                error=error,
                retry=decision.retry,
                retry_delay_seconds=decision.delay_seconds,
            )
            if updated is None:
                await session.rollback()
                logger.warning(
                    "execution.lease_lost_on_failure",
                    job_id=str(job.id),
                    worker_id=str(worker_id),
                )
                return False
            await AttemptRepository(session).finish(
                job.id, job.attempt_count, AttemptStatus.FAILED, error=error
            )
            await session.commit()
            logger.info(
                "execution.job_failed",
                job_id=str(job.id),
                attempt=job.attempt_count,
                will_retry=decision.retry,
                retry_delay_seconds=round(decision.delay_seconds, 2),
            )

        # Fire-and-forget AI summary for DLQ jobs (outside the main txn)
        if not decision.retry and self.bus:
            try:
                ai = AISummaryService(self.bus)
                summary = await ai.generate_summary(
                    error, job.task_name, job.attempt_count
                )
                if summary:
                    async with self.session_factory() as session:
                        await JobRepository(session).set_ai_summary(job.id, summary)
                        await session.commit()
                    logger.info(
                        "ai_summary.stored", job_id=str(job.id),
                    )
            except Exception:
                logger.warning(
                    "ai_summary.failed", job_id=str(job.id), exc_info=True,
                )

        return True

    async def release_job(self, job: Job, worker_id: uuid.UUID) -> bool:
        """Graceful-shutdown path: return the job to PENDING immediately
        and refund the attempt (the interruption is not the job's fault)."""
        async with self.session_factory() as session:
            updated = await JobRepository(session).release(job.id, worker_id)
            if updated is None:
                await session.rollback()
                return False
            await AttemptRepository(session).finish(
                job.id,
                job.attempt_count,
                AttemptStatus.ABORTED,
                error="worker shut down before the job finished",
            )
            await session.commit()
            logger.info("execution.job_released", job_id=str(job.id))
            return True

    # ------------------------------------------------------------------
    # reaper
    # ------------------------------------------------------------------

    async def reap_once(self, offline_after_seconds: int) -> int:
        """One reaper sweep. Returns the number of leases reclaimed.

        Guarded by a transaction-scoped advisory lock so any number of
        workers can host a reaper loop while exactly one sweep runs at a
        time (no leader election infrastructure needed).
        """
        async with self.session_factory() as session:
            got_lock = (
                await session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": REAPER_ADVISORY_LOCK_KEY},
                )
            ).scalar()
            if not got_lock:
                await session.rollback()
                return 0

            job_repo = JobRepository(session)
            attempt_repo = AttemptRepository(session)

            expired = await job_repo.select_expired_for_update()
            for job in expired:
                error = (
                    f"lease expired (worker {job.locked_by} presumed dead "
                    f"after missing heartbeats)"
                )
                await attempt_repo.finish(
                    job.id, job.attempt_count, AttemptStatus.LOST, error=error
                )
                decision = decide_failure(_policy_of(job), job.attempt_count)
                # We hold FOR UPDATE row locks, so direct assignment is safe
                # here; concurrent finalizers block and then see the new state.
                job.locked_by = None
                job.lease_expires_at = None
                job.last_error = error
                db_now = (
                    await session.execute(text("SELECT now()"))
                ).scalar_one()
                if decision.retry:
                    job.status = JobStatus.PENDING
                    job.run_at = db_now + dt.timedelta(
                        seconds=decision.delay_seconds
                    )
                else:
                    job.status = JobStatus.DEAD
                    job.finished_at = db_now
                logger.warning(
                    "reaper.lease_reclaimed",
                    job_id=str(job.id),
                    attempt=job.attempt_count,
                    will_retry=decision.retry,
                )

            stale = await WorkerRepository(session).mark_stale_offline(
                offline_after_seconds
            )
            if stale:
                logger.warning("reaper.workers_marked_offline", count=stale)

            await session.commit()
            return len(expired)

    # ------------------------------------------------------------------
    # schedule materializer (cron)
    # ------------------------------------------------------------------

    async def materialize_schedules_once(self) -> int:
        """Turn due schedules into jobs. Returns the number of jobs created.

        Advisory-locked like the reaper: every worker hosts the loop, one
        sweep runs cluster-wide. Firing is exactly-once because the job
        insert and the cursor advance commit atomically, and the job
        carries idempotency_key "schedule:<id>:<fire time>" as a backstop —
        even a replayed sweep cannot double-fire a tick.

        Missed ticks (scheduler down, schedule paused) collapse into a
        single firing: the cursor jumps to the next tick *after now*, so a
        recovering system runs each schedule once instead of flooding the
        queue with every tick it slept through.
        """
        async with self.session_factory() as session:
            got_lock = (
                await session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": SCHEDULER_ADVISORY_LOCK_KEY},
                )
            ).scalar()
            if not got_lock:
                await session.rollback()
                return 0

            schedule_repo = ScheduleRepository(session)
            job_repo = JobRepository(session)
            due = await schedule_repo.select_due_for_update()
            if not due:
                await session.rollback()
                return 0

            db_now = (await session.execute(text("SELECT now()"))).scalar_one()
            created = 0
            for s in due:
                fire_key = f"schedule:{s.id}:{s.next_run_at.isoformat()}"
                existing = await job_repo.get_by_idempotency_key(
                    s.owner_id, fire_key
                )
                if existing is None:
                    job_repo.add(
                        Job(
                            owner_id=s.owner_id,
                            queue_id=s.queue_id,
                            queue=s.queue,
                            task_name=s.task_name,
                            payload=s.payload,
                            priority=s.priority,
                            max_attempts=s.max_attempts,
                            timeout_seconds=s.timeout_seconds,
                            backoff_strategy=s.backoff_strategy,
                            backoff_base_seconds=s.backoff_base_seconds,
                            backoff_factor=s.backoff_factor,
                            backoff_max_seconds=s.backoff_max_seconds,
                            idempotency_key=fire_key,
                            run_at=s.next_run_at,
                        )
                    )
                    created += 1
                s.last_run_at = s.next_run_at
                s.next_run_at = next_run_after(
                    s.cron_expr, max(s.next_run_at, db_now)
                )
                logger.info(
                    "scheduler.fired",
                    schedule_id=str(s.id),
                    task_name=s.task_name,
                    next_run_at=s.next_run_at.isoformat(),
                )

            await session.commit()
            return created
