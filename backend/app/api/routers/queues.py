import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, Queues
from app.models.user import UserRole
from app.schemas.queue import (
    QueueCreate,
    QueueRead,
    QueueStats,
    QueueUpdate,
    QueueWithStats,
)
from app.services.exceptions import ConflictError, NotFoundError
from app.services.rbac import require_role

router = APIRouter(prefix="/queues", tags=["queues"])


def _with_stats(queue, counts: dict[str, int]) -> QueueWithStats:
    return QueueWithStats(
        **QueueRead.model_validate(queue).model_dump(), counts_by_status=counts
    )


@router.get(
    "",
    response_model=list[QueueWithStats],
    summary="List queues with per-queue job counts",
)
async def list_queues(
    user: CurrentUser,
    queues: Queues,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[QueueWithStats]:
    pairs = await queues.list_with_stats(user, project_id=project_id)
    return [_with_stats(q, counts) for q, counts in pairs]


@router.post(
    "",
    response_model=QueueRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a queue",
    description=(
        "Queues are also auto-created on first use by name; use this "
        "endpoint to create one with non-default configuration up front."
    ),
)
async def create_queue(
    data: QueueCreate, user: CurrentUser, queues: Queues
) -> QueueRead:
    require_role(user, UserRole.ADMIN)
    try:
        queue = await queues.create(user, data)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    except ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail) from None
    return QueueRead.model_validate(queue)


@router.get("/{queue_id}", response_model=QueueRead, summary="Get a queue")
async def get_queue(
    queue_id: uuid.UUID, user: CurrentUser, queues: Queues
) -> QueueRead:
    try:
        queue = await queues.get(user, queue_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return QueueRead.model_validate(queue)


@router.patch(
    "/{queue_id}",
    response_model=QueueRead,
    summary="Update queue configuration",
    description=(
        "Partial update. Changing defaults affects future jobs only — "
        "jobs snapshot their policy at enqueue time. Set max_concurrency "
        "to null to remove the cap."
    ),
)
async def update_queue(
    queue_id: uuid.UUID, data: QueueUpdate, user: CurrentUser, queues: Queues
) -> QueueRead:
    require_role(user, UserRole.ADMIN)
    try:
        queue = await queues.update(user, queue_id, data)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return QueueRead.model_validate(queue)


@router.post(
    "/{queue_id}/pause",
    response_model=QueueRead,
    summary="Pause a queue",
    description=(
        "Paused queues stop handing out new jobs immediately; jobs already "
        "running finish normally. Enqueueing stays open."
    ),
)
async def pause_queue(
    queue_id: uuid.UUID, user: CurrentUser, queues: Queues
) -> QueueRead:
    require_role(user, UserRole.ADMIN)
    try:
        queue = await queues.set_paused(user, queue_id, True)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return QueueRead.model_validate(queue)


@router.post(
    "/{queue_id}/resume", response_model=QueueRead, summary="Resume a queue"
)
async def resume_queue(
    queue_id: uuid.UUID, user: CurrentUser, queues: Queues
) -> QueueRead:
    require_role(user, UserRole.ADMIN)
    try:
        queue = await queues.set_paused(user, queue_id, False)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return QueueRead.model_validate(queue)


@router.get(
    "/{queue_id}/stats",
    response_model=QueueStats,
    summary="Per-queue job statistics",
)
async def queue_stats(
    queue_id: uuid.UUID, user: CurrentUser, queues: Queues
) -> QueueStats:
    try:
        queue, counts = await queues.stats(user, queue_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return QueueStats(
        queue_id=queue.id,
        name=queue.name,
        paused=queue.paused,
        max_concurrency=queue.max_concurrency,
        counts_by_status=counts,
    )
