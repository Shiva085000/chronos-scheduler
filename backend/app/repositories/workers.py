import uuid

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Worker, WorkerStatus


class WorkerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, worker: Worker) -> Worker:
        self.session.add(worker)
        return worker

    async def list_all(self, limit: int = 100) -> list[Worker]:
        result = await self.session.execute(
            select(Worker)
            .order_by(Worker.status, Worker.last_heartbeat_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def heartbeat(self, worker_id: uuid.UUID) -> int:
        await self.session.execute(
            update(Worker)
            .where(Worker.id == worker_id)
            .values(last_heartbeat_at=func.now())
            .execution_options(synchronize_session=False)
        )
        # Self-heal false death: if the reaper declared this worker offline
        # during a transient stall (host sleep, paused container, >60s DB
        # outage), the fact that we are heartbeating again proves it wrong.
        # Without this, a live worker stays "offline" in the fleet view and
        # stats forever. OFFLINE only — a DRAINING worker must stay draining.
        result = await self.session.execute(
            update(Worker)
            .where(Worker.id == worker_id, Worker.status == WorkerStatus.OFFLINE)
            .values(status=WorkerStatus.ONLINE, stopped_at=None)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0

    async def set_status(
        self, worker_id: uuid.UUID, status: WorkerStatus, *, stopped: bool = False
    ) -> None:
        values: dict = {"status": status}
        if stopped:
            values["stopped_at"] = func.now()
        await self.session.execute(
            update(Worker)
            .where(Worker.id == worker_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    async def mark_stale_offline(self, offline_after_seconds: int) -> int:
        """Reaper: declare workers dead after missing heartbeats long enough."""
        result = await self.session.execute(
            update(Worker)
            .where(
                Worker.status != WorkerStatus.OFFLINE,
                Worker.last_heartbeat_at
                < text(f"now() - interval '{int(offline_after_seconds)} seconds'"),
            )
            .values(status=WorkerStatus.OFFLINE, stopped_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0

    async def max_heartbeat_lag_seconds(self) -> float:
        """Worst heartbeat staleness across ONLINE workers. Rises toward
        the lease window when a worker is wedged or partitioned."""
        result = await self.session.execute(
            select(
                func.extract("epoch", func.now() - func.min(Worker.last_heartbeat_at))
            ).where(Worker.status == WorkerStatus.ONLINE)
        )
        value = result.scalar()
        return float(value) if value is not None else 0.0

    async def count_online(self) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Worker)
            .where(Worker.status == WorkerStatus.ONLINE)
        )
        return result.scalar_one()
