"""User-facing job operations: enqueue, list, cancel, DLQ management."""

import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventBus
from app.models import Job, JobAttempt, JobStatus
from app.repositories.attempts import AttemptRepository
from app.repositories.jobs import JobRepository
from app.schemas.job import JobCreate
from app.services.exceptions import ConflictError, NotFoundError

logger = structlog.get_logger(__name__)


class JobService:
    def __init__(self, session: AsyncSession, bus: EventBus) -> None:
        self.session = session
        self.bus = bus
        self.jobs = JobRepository(session)
        self.attempts = AttemptRepository(session)

    async def enqueue(
        self, owner_id: uuid.UUID, data: JobCreate
    ) -> tuple[Job, bool]:
        """Enqueue a job. Returns (job, created).

        Idempotency: if a key is supplied and a job with that key already
        exists for this owner, the existing job is returned untouched.
        The unique partial index is the authority — the pre-check is only
        a fast path; a concurrent duplicate loses the INSERT race and is
        recovered via IntegrityError.
        """
        if data.idempotency_key:
            existing = await self.jobs.get_by_idempotency_key(
                owner_id, data.idempotency_key
            )
            if existing is not None:
                return existing, False

        job = Job(
            owner_id=owner_id,
            queue=data.queue,
            task_name=data.task_name,
            payload=data.payload,
            priority=data.priority,
            max_attempts=data.max_attempts,
            timeout_seconds=data.timeout_seconds,
            backoff_base_seconds=data.backoff_base_seconds,
            backoff_factor=data.backoff_factor,
            backoff_max_seconds=data.backoff_max_seconds,
            idempotency_key=data.idempotency_key,
        )
        if data.run_at is not None:
            job.run_at = data.run_at
        self.jobs.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if data.idempotency_key:
                existing = await self.jobs.get_by_idempotency_key(
                    owner_id, data.idempotency_key
                )
                if existing is not None:
                    return existing, False
            raise

        # Post-commit on purpose: a wake for an uncommitted job would race
        # workers against a row they cannot see yet.
        await self.bus.publish_wake()
        logger.info(
            "job.enqueued",
            job_id=str(job.id),
            task_name=job.task_name,
            queue=job.queue,
        )
        return job, True

    async def get(self, owner_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        job = await self.jobs.get_for_owner(job_id, owner_id)
        if job is None:
            raise NotFoundError("job not found")
        return job

    async def list_jobs(
        self,
        owner_id: uuid.UUID,
        *,
        status: JobStatus | None = None,
        queue: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        return await self.jobs.list_for_owner(
            owner_id, status=status, queue=queue, limit=limit, offset=offset
        )

    async def list_attempts(
        self, owner_id: uuid.UUID, job_id: uuid.UUID
    ) -> list[JobAttempt]:
        await self.get(owner_id, job_id)  # ownership check
        return await self.attempts.list_for_job(job_id)

    async def cancel(self, owner_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        job = await self.jobs.cancel(job_id, owner_id)
        if job is None:
            await self.get(owner_id, job_id)  # raises NotFound if unknown
            raise ConflictError("only pending jobs can be cancelled")
        await self.session.commit()
        logger.info("job.cancelled", job_id=str(job_id))
        return job

    async def requeue(self, owner_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        """Requeue a DEAD (DLQ) or CANCELLED job with a fresh attempt budget."""
        job = await self.jobs.requeue(job_id, owner_id)
        if job is None:
            await self.get(owner_id, job_id)
            raise ConflictError("only dead or cancelled jobs can be requeued")
        await self.session.commit()
        await self.bus.publish_wake()
        logger.info("job.requeued", job_id=str(job_id))
        return job
