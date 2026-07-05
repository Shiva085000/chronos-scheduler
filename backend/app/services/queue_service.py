"""Queue management: configuration, pause/resume, per-queue statistics.

Pause and concurrency changes take effect at the *next claim attempt* —
no worker coordination needed, because the claim query reads the queue
row inside the claiming transaction. Running jobs are never interrupted
by a pause; the queue drains naturally.
"""

import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobStatus, Queue, User
from app.repositories.queues import QueueRepository
from app.repositories.tenancy import TenancyRepository
from app.schemas.queue import QueueCreate, QueueUpdate
from app.services.exceptions import ConflictError, NotFoundError

logger = structlog.get_logger(__name__)

_EMPTY_COUNTS = {s.value: 0 for s in JobStatus}


class QueueService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.queues = QueueRepository(session)
        self.tenancy = TenancyRepository(session)

    async def get(self, user: User, queue_id: uuid.UUID) -> Queue:
        queue = await self.queues.get_for_org(queue_id, user.org_id)
        if queue is None:
            raise NotFoundError("queue not found")
        return queue

    async def list_with_stats(
        self, user: User, *, project_id: uuid.UUID | None = None
    ) -> list[tuple[Queue, dict[str, int]]]:
        queues = await self.queues.list_for_org(user.org_id, project_id=project_id)
        counts = await self.queues.counts_by_queue_and_status(user.org_id)
        return [(q, counts.get(q.id, dict(_EMPTY_COUNTS))) for q in queues]

    async def stats(self, user: User, queue_id: uuid.UUID) -> tuple[Queue, dict[str, int]]:
        queue = await self.get(user, queue_id)
        counts = await self.queues.counts_by_queue_and_status(user.org_id)
        return queue, counts.get(queue.id, dict(_EMPTY_COUNTS))

    async def create(self, user: User, data: QueueCreate) -> Queue:
        if data.project_id is not None:
            project = await self.tenancy.get_project(data.project_id, user.org_id)
            if project is None:
                raise NotFoundError("project not found")
        else:
            project = await self.tenancy.default_project(user.org_id)
            if project is None:  # pragma: no cover — created at registration
                raise NotFoundError("organization has no projects")

        queue = self.queues.add(
            Queue(
                project_id=project.id,
                name=data.name,
                shard_key=data.shard_key,
                max_concurrency=data.max_concurrency,
                default_priority=data.default_priority,
                default_max_attempts=data.default_max_attempts,
                default_backoff_strategy=data.default_backoff_strategy,
                default_backoff_base_seconds=data.default_backoff_base_seconds,
                default_backoff_factor=data.default_backoff_factor,
                default_backoff_max_seconds=data.default_backoff_max_seconds,
                default_timeout_seconds=data.default_timeout_seconds,
            )
        )
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                f"a queue named {data.name!r} already exists in that project"
            ) from None
        logger.info("queue.created", queue_id=str(queue.id), name=queue.name)
        return queue

    async def update(
        self, user: User, queue_id: uuid.UUID, data: QueueUpdate
    ) -> Queue:
        """Apply only the fields present in the request (PATCH semantics).

        `max_concurrency` sent as null means "remove the cap"; absent means
        "leave it alone" — model_fields_set tells the two apart.
        """
        queue = await self.get(user, queue_id)
        for field in data.model_fields_set:
            setattr(queue, field, getattr(data, field))
        await self.session.commit()
        logger.info(
            "queue.updated",
            queue_id=str(queue_id),
            fields=sorted(data.model_fields_set),
        )
        return queue

    async def set_paused(
        self, user: User, queue_id: uuid.UUID, paused: bool
    ) -> Queue:
        queue = await self.get(user, queue_id)
        queue.paused = paused
        await self.session.commit()
        logger.info(
            "queue.paused" if paused else "queue.resumed", queue_id=str(queue_id)
        )
        return queue
