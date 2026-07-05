"""Queue data access.

Queues are org-scoped through their project; every accessor takes the
caller's org_id so ownership enforcement happens in the query, not in
router code.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus, Project, Queue


class QueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, queue: Queue) -> Queue:
        self.session.add(queue)
        return queue

    def _org_scoped(self):
        return select(Queue).join(Project, Project.id == Queue.project_id)

    async def get_for_org(
        self, queue_id: uuid.UUID, org_id: uuid.UUID
    ) -> Queue | None:
        result = await self.session.execute(
            self._org_scoped().where(Queue.id == queue_id, Project.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(
        self, project_id: uuid.UUID, name: str
    ) -> Queue | None:
        result = await self.session.execute(
            select(Queue).where(Queue.project_id == project_id, Queue.name == name)
        )
        return result.scalar_one_or_none()

    async def list_for_org(
        self, org_id: uuid.UUID, *, project_id: uuid.UUID | None = None
    ) -> list[Queue]:
        stmt = self._org_scoped().where(Project.org_id == org_id)
        if project_id is not None:
            stmt = stmt.where(Queue.project_id == project_id)
        stmt = stmt.order_by(Queue.created_at.asc())
        return list((await self.session.execute(stmt)).scalars())

    async def counts_by_queue_and_status(
        self, org_id: uuid.UUID
    ) -> dict[uuid.UUID, dict[str, int]]:
        """{queue_id: {status: count}} for every queue in the org, one scan."""
        rows = await self.session.execute(
            select(Job.queue_id, Job.status, func.count())
            .join(Queue, Queue.id == Job.queue_id)
            .join(Project, Project.id == Queue.project_id)
            .where(Project.org_id == org_id)
            .group_by(Job.queue_id, Job.status)
        )
        counts: dict[uuid.UUID, dict[str, int]] = {}
        for queue_id, status, count in rows:
            counts.setdefault(queue_id, {s.value: 0 for s in JobStatus})[
                status.value
            ] = count
        return counts
