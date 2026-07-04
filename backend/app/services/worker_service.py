from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Worker
from app.repositories.workers import WorkerRepository


class WorkerService:
    def __init__(self, session: AsyncSession) -> None:
        self.workers = WorkerRepository(session)

    async def list_workers(self) -> list[Worker]:
        return await self.workers.list_all()
