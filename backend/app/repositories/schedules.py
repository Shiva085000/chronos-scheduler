"""Schedule data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Schedule


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, schedule: Schedule) -> Schedule:
        self.session.add(schedule)
        return schedule

    async def get_for_owner(
        self, schedule_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Schedule | None:
        result = await self.session.execute(
            select(Schedule).where(
                Schedule.id == schedule_id, Schedule.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self, owner_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Schedule], int]:
        conditions = [Schedule.owner_id == owner_id]
        total = (
            await self.session.execute(
                select(func.count()).select_from(Schedule).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(Schedule)
                .where(*conditions)
                .order_by(Schedule.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return list(rows), total

    async def delete(self, schedule: Schedule) -> None:
        await self.session.delete(schedule)

    async def select_due_for_update(self, limit: int = 100) -> list[Schedule]:
        """Lock a batch of due, unpaused schedules for the materializer.

        SKIP LOCKED mirrors the reaper: a schedule being edited or
        materialized elsewhere is passed over, not fought over.
        """
        result = await self.session.execute(
            select(Schedule)
            .where(Schedule.paused.is_(False), Schedule.next_run_at <= func.now())
            .order_by(Schedule.next_run_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars())
