"""Dead letter queue endpoints.

The DLQ is a *view* over jobs with status = DEAD, not a separate storage
system — a deliberate choice so a dead job keeps its full identity,
payload and attempt history when it is inspected or requeued.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, Jobs
from app.models.job import JobStatus
from app.schemas.common import Page
from app.schemas.job import JobRead
from app.services.exceptions import ConflictError, NotFoundError

router = APIRouter(prefix="/dlq", tags=["dead letter queue"])


@router.get("", response_model=Page[JobRead], summary="List dead jobs")
async def list_dlq(
    user: CurrentUser,
    jobs: Jobs,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[JobRead]:
    items, total = await jobs.list_jobs(
        user.id, status=JobStatus.DEAD, limit=limit, offset=offset
    )
    return Page(
        items=[JobRead.model_validate(j) for j in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{job_id}/requeue",
    response_model=JobRead,
    summary="Requeue a dead job with a fresh attempt budget",
)
async def requeue_dead_job(
    job_id: uuid.UUID, user: CurrentUser, jobs: Jobs
) -> JobRead:
    try:
        job = await jobs.requeue(user.id, job_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    except ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail) from None
    return JobRead.model_validate(job)
