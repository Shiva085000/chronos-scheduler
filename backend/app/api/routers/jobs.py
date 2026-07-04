import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, Jobs
from app.models.job import JobStatus
from app.schemas.common import Page
from app.schemas.job import AttemptRead, JobCreate, JobRead
from app.services.exceptions import ConflictError, NotFoundError

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enqueue a job",
    description=(
        "Enqueue a job for asynchronous execution. Supply an idempotency "
        "key (body field or `Idempotency-Key` header) to make retried "
        "submissions safe: a duplicate returns the existing job with "
        "status 200 instead of creating a second one."
    ),
)
async def create_job(
    data: JobCreate,
    user: CurrentUser,
    jobs: Jobs,
    response: Response,
    idempotency_key_header: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
) -> JobRead:
    if idempotency_key_header and not data.idempotency_key:
        data.idempotency_key = idempotency_key_header
    job, created = await jobs.enqueue(user.id, data)
    if not created:
        response.status_code = status.HTTP_200_OK
    return JobRead.model_validate(job)


@router.get("", response_model=Page[JobRead], summary="List my jobs")
async def list_jobs(
    user: CurrentUser,
    jobs: Jobs,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    queue: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[JobRead]:
    items, total = await jobs.list_jobs(
        user.id, status=status_filter, queue=queue, limit=limit, offset=offset
    )
    return Page(
        items=[JobRead.model_validate(j) for j in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobRead, summary="Get a job")
async def get_job(job_id: uuid.UUID, user: CurrentUser, jobs: Jobs) -> JobRead:
    try:
        job = await jobs.get(user.id, job_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return JobRead.model_validate(job)


@router.get(
    "/{job_id}/attempts",
    response_model=list[AttemptRead],
    summary="Execution history of a job",
)
async def list_attempts(
    job_id: uuid.UUID, user: CurrentUser, jobs: Jobs
) -> list[AttemptRead]:
    try:
        attempts = await jobs.list_attempts(user.id, job_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return [AttemptRead.model_validate(a) for a in attempts]


@router.post(
    "/{job_id}/cancel",
    response_model=JobRead,
    summary="Cancel a pending job",
    description=(
        "Only PENDING jobs can be cancelled. A RUNNING job cannot be "
        "cancelled because the worker is already executing it; wait for "
        "the attempt to finish."
    ),
)
async def cancel_job(job_id: uuid.UUID, user: CurrentUser, jobs: Jobs) -> JobRead:
    try:
        job = await jobs.cancel(user.id, job_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    except ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail) from None
    return JobRead.model_validate(job)


@router.post(
    "/{job_id}/requeue",
    response_model=JobRead,
    summary="Requeue a dead or cancelled job",
)
async def requeue_job(job_id: uuid.UUID, user: CurrentUser, jobs: Jobs) -> JobRead:
    try:
        job = await jobs.requeue(user.id, job_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    except ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail) from None
    return JobRead.model_validate(job)
