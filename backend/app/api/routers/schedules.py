import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, Schedules
from app.schemas.common import Page
from app.schemas.schedule import ScheduleCreate, ScheduleRead, ScheduleUpdate
from app.services.exceptions import NotFoundError

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.post(
    "",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a recurring (cron) schedule",
    description=(
        "Fires per a standard 5-field cron expression, evaluated in UTC. "
        "Each firing materializes an ordinary job with this schedule's "
        "retry policy; missed ticks (downtime, pause) collapse into a "
        "single firing instead of a backlog flood."
    ),
)
async def create_schedule(
    data: ScheduleCreate, user: CurrentUser, schedules: Schedules
) -> ScheduleRead:
    try:
        schedule = await schedules.create(user, data)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return ScheduleRead.model_validate(schedule)


@router.get("", response_model=Page[ScheduleRead], summary="List my schedules")
async def list_schedules(
    user: CurrentUser,
    schedules: Schedules,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ScheduleRead]:
    items, total = await schedules.list(user, limit=limit, offset=offset)
    return Page(
        items=[ScheduleRead.model_validate(s) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{schedule_id}", response_model=ScheduleRead, summary="Get a schedule")
async def get_schedule(
    schedule_id: uuid.UUID, user: CurrentUser, schedules: Schedules
) -> ScheduleRead:
    try:
        schedule = await schedules.get(user, schedule_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return ScheduleRead.model_validate(schedule)


@router.patch(
    "/{schedule_id}",
    response_model=ScheduleRead,
    summary="Update a schedule",
    description=(
        "Partial update. Changing cron_expr — or resuming via "
        "paused=false — recomputes the next fire time from now."
    ),
)
async def update_schedule(
    schedule_id: uuid.UUID,
    data: ScheduleUpdate,
    user: CurrentUser,
    schedules: Schedules,
) -> ScheduleRead:
    try:
        schedule = await schedules.update(user, schedule_id, data)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return ScheduleRead.model_validate(schedule)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule",
    description="Jobs already materialized from this schedule are unaffected.",
)
async def delete_schedule(
    schedule_id: uuid.UUID, user: CurrentUser, schedules: Schedules
) -> Response:
    try:
        await schedules.delete(user, schedule_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
