"""Recurring-schedule management (the materializer lives in
ExecutionService.schedule_once; this service is the user-facing CRUD).

`next_run_at` is always computed from the *database* clock, keeping the
no-worker-clock-arithmetic invariant: the API host's clock never decides
when a schedule fires.
"""

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cron import next_run_after
from app.models import Queue, Schedule, User
from app.repositories.queues import QueueRepository
from app.repositories.schedules import ScheduleRepository
from app.repositories.tenancy import TenancyRepository
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate
from app.services.exceptions import NotFoundError

logger = structlog.get_logger(__name__)


class ScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.schedules = ScheduleRepository(session)
        self.queues = QueueRepository(session)
        self.tenancy = TenancyRepository(session)

    async def _db_now(self):
        return (await self.session.execute(text("SELECT now()"))).scalar_one()

    async def _resolve_queue(
        self, user: User, project_id: uuid.UUID | None, name: str
    ) -> Queue:
        if project_id is not None:
            project = await self.tenancy.get_project(project_id, user.org_id)
            if project is None:
                raise NotFoundError("project not found")
        else:
            project = await self.tenancy.default_project(user.org_id)
            if project is None:  # pragma: no cover — created at registration
                raise NotFoundError("organization has no projects")
        queue = await self.queues.get_by_name(project.id, name)
        if queue is None:
            queue = self.queues.add(Queue(project_id=project.id, name=name))
            await self.session.flush()
        return queue

    async def create(self, user: User, data: ScheduleCreate) -> Schedule:
        queue = await self._resolve_queue(user, data.project_id, data.queue)
        now = await self._db_now()
        schedule = self.schedules.add(
            Schedule(
                owner_id=user.id,
                queue_id=queue.id,
                queue=queue.name,
                task_name=data.task_name,
                payload=data.payload,
                cron_expr=data.cron_expr,
                priority=data.priority,
                max_attempts=data.max_attempts,
                timeout_seconds=data.timeout_seconds,
                backoff_strategy=data.backoff_strategy,
                backoff_base_seconds=data.backoff_base_seconds,
                backoff_factor=data.backoff_factor,
                backoff_max_seconds=data.backoff_max_seconds,
                next_run_at=next_run_after(data.cron_expr, now),
            )
        )
        await self.session.commit()
        logger.info(
            "schedule.created",
            schedule_id=str(schedule.id),
            cron=data.cron_expr,
            task_name=data.task_name,
        )
        return schedule

    async def get(self, user: User, schedule_id: uuid.UUID) -> Schedule:
        schedule = await self.schedules.get_for_owner(schedule_id, user.id)
        if schedule is None:
            raise NotFoundError("schedule not found")
        return schedule

    async def list(
        self, user: User, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Schedule], int]:
        return await self.schedules.list_for_owner(
            user.id, limit=limit, offset=offset
        )

    async def update(
        self, user: User, schedule_id: uuid.UUID, data: ScheduleUpdate
    ) -> Schedule:
        schedule = await self.get(user, schedule_id)
        for field in data.model_fields_set:
            setattr(schedule, field, getattr(data, field))
        if "cron_expr" in data.model_fields_set or (
            "paused" in data.model_fields_set and not data.paused
        ):
            # New expression — or waking from pause, where firing "now" for
            # ticks missed while paused would surprise; both restart from
            # the next future tick.
            schedule.next_run_at = next_run_after(
                schedule.cron_expr, await self._db_now()
            )
        await self.session.commit()
        logger.info(
            "schedule.updated",
            schedule_id=str(schedule_id),
            fields=sorted(data.model_fields_set),
        )
        return schedule

    async def delete(self, user: User, schedule_id: uuid.UUID) -> None:
        schedule = await self.get(user, schedule_id)
        await self.schedules.delete(schedule)
        await self.session.commit()
        logger.info("schedule.deleted", schedule_id=str(schedule_id))
