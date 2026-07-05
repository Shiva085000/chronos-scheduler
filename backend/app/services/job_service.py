"""User-facing job operations: enqueue (single and batch), list, cancel,
DLQ management.

Enqueue resolves the target queue row first (creating it on first use —
queues are addresses, and forcing a two-step create-queue-then-enqueue
dance buys no safety since the row carries defaults, not permissions),
then denormalizes the *effective* retry policy onto the job: any policy
field the client did not explicitly send inherits the queue's default.
`model_fields_set` distinguishes "omitted" from "sent the default value".
"""

import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventBus
from app.models import Job, JobAttempt, JobStatus, Queue, User
from app.models.job_dependency import JobDependency
from app.repositories.attempts import AttemptRepository
from app.repositories.dependencies import DependencyRepository
from app.repositories.jobs import JobRepository
from app.repositories.queues import QueueRepository
from app.repositories.tenancy import TenancyRepository
from app.schemas.job import JobCreate
from app.services.exceptions import ConflictError, NotFoundError

logger = structlog.get_logger(__name__)


class JobService:
    def __init__(self, session: AsyncSession, bus: EventBus) -> None:
        self.session = session
        self.bus = bus
        self.jobs = JobRepository(session)
        self.attempts = AttemptRepository(session)
        self.queues = QueueRepository(session)
        self.tenancy = TenancyRepository(session)
        self.deps = DependencyRepository(session)

    # ------------------------------------------------------------------
    # queue resolution
    # ------------------------------------------------------------------

    async def _resolve_queue(
        self, user: User, project_id: uuid.UUID | None, name: str
    ) -> Queue:
        """Find (or create on first use) the queue `name` in the given
        project, defaulting to the org's default project."""
        if project_id is not None:
            project = await self.tenancy.get_project(project_id, user.org_id)
            if project is None:
                raise NotFoundError("project not found")
        else:
            project = await self.tenancy.default_project(user.org_id)
            if project is None:  # pragma: no cover — created at registration
                raise NotFoundError("organization has no projects")

        queue = await self.queues.get_by_name(project.id, name)
        if queue is not None:
            return queue
        try:
            # SAVEPOINT so losing the create race rolls back only this
            # insert, never work already staged in the enclosing
            # transaction (e.g. earlier jobs of a batch).
            async with self.session.begin_nested():
                queue = self.queues.add(Queue(project_id=project.id, name=name))
                await self.session.flush()
        except IntegrityError:
            # Lost a create race; the winner's row is what we wanted anyway.
            queue = await self.queues.get_by_name(project.id, name)
            if queue is None:
                raise
        return queue

    def _build_job(
        self,
        owner_id: uuid.UUID,
        queue: Queue,
        data: JobCreate,
        *,
        batch_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
    ) -> Job:
        sent = data.model_fields_set

        def effective(field: str, default_value):
            return getattr(data, field) if field in sent else default_value

        job = Job(
            owner_id=owner_id,
            queue_id=queue.id,
            queue=queue.name,
            task_name=data.task_name,
            payload=data.payload,
            batch_id=batch_id,
            workflow_id=workflow_id,
            priority=effective("priority", queue.default_priority),
            max_attempts=effective("max_attempts", queue.default_max_attempts),
            timeout_seconds=effective(
                "timeout_seconds", queue.default_timeout_seconds
            ),
            backoff_strategy=effective(
                "backoff_strategy", queue.default_backoff_strategy
            ),
            backoff_base_seconds=effective(
                "backoff_base_seconds", queue.default_backoff_base_seconds
            ),
            backoff_factor=effective("backoff_factor", queue.default_backoff_factor),
            backoff_max_seconds=effective(
                "backoff_max_seconds", queue.default_backoff_max_seconds
            ),
            idempotency_key=data.idempotency_key,
        )
        if data.run_at is not None:
            job.run_at = data.run_at
        return job

    # ------------------------------------------------------------------
    # enqueue
    # ------------------------------------------------------------------

    async def enqueue(self, user: User, data: JobCreate) -> tuple[Job, bool]:
        """Enqueue a job. Returns (job, created).

        Idempotency: if a key is supplied and a job with that key already
        exists for this owner, the existing job is returned untouched.
        The unique partial index is the authority — the pre-check is only
        a fast path; a concurrent duplicate loses the INSERT race and is
        recovered via IntegrityError.
        """
        if data.idempotency_key:
            existing = await self.jobs.get_by_idempotency_key(
                user.id, data.idempotency_key
            )
            if existing is not None:
                return existing, False

        queue = await self._resolve_queue(user, data.project_id, data.queue)

        # Workflow dependencies
        workflow_id = None
        if data.depends_on:
            workflow_id = uuid.uuid4()
            # Verify all dependency targets exist and belong to same owner
            for dep_id in data.depends_on:
                dep_job = await self.jobs.get_for_owner(dep_id, user.id)
                if dep_job is None:
                    raise NotFoundError(
                        f"dependency target {dep_id} not found or not owned by you"
                    )
                # Inherit the workflow_id from upstream if it already has one
                if dep_job.workflow_id:
                    workflow_id = dep_job.workflow_id

        job = self.jobs.add(
            self._build_job(user.id, queue, data, workflow_id=workflow_id)
        )
        try:
            await self.session.flush()  # get job.id before inserting deps
            if data.depends_on:
                await self.deps.add_many(job.id, data.depends_on)
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if data.idempotency_key:
                existing = await self.jobs.get_by_idempotency_key(
                    user.id, data.idempotency_key
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

    async def enqueue_batch(
        self, user: User, items: list[JobCreate]
    ) -> tuple[uuid.UUID, list[Job]]:
        """Enqueue a batch atomically: one transaction, one shared batch_id.

        All-or-nothing on purpose — a partially created batch is the worst
        of both worlds (the client can neither retry nor trust it). An
        idempotency-key collision anywhere rejects the whole batch as 409.
        """
        batch_id = uuid.uuid4()
        queue_cache: dict[tuple[uuid.UUID | None, str], Queue] = {}
        jobs: list[Job] = []
        for data in items:
            key = (data.project_id, data.queue)
            queue = queue_cache.get(key)
            if queue is None:
                queue = await self._resolve_queue(user, data.project_id, data.queue)
                queue_cache[key] = queue
            jobs.append(
                self.jobs.add(self._build_job(user.id, queue, data, batch_id=batch_id))
            )
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                "an idempotency key in the batch collides with an existing "
                "job (or repeats within the batch); the batch was rejected"
            ) from None

        await self.bus.publish_wake()
        logger.info("job.batch_enqueued", batch_id=str(batch_id), size=len(jobs))
        return batch_id, jobs

    # ------------------------------------------------------------------
    # read / transitions
    # ------------------------------------------------------------------

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
        batch_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        return await self.jobs.list_for_owner(
            owner_id,
            status=status,
            queue=queue,
            batch_id=batch_id,
            limit=limit,
            offset=offset,
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
