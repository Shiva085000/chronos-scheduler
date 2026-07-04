from fastapi import APIRouter

from app.api.deps import CurrentUser, Workers
from app.schemas.worker import WorkerRead

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("", response_model=list[WorkerRead], summary="List worker fleet")
async def list_workers(_: CurrentUser, workers: Workers) -> list[WorkerRead]:
    return [WorkerRead.model_validate(w) for w in await workers.list_workers()]
