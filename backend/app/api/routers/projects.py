import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, Tenancy
from app.schemas.tenancy import OrganizationRead, ProjectCreate, ProjectRead
from app.services.exceptions import ConflictError, NotFoundError

router = APIRouter(tags=["projects"])


@router.get(
    "/org",
    response_model=OrganizationRead,
    summary="My organization",
)
async def get_org(user: CurrentUser, tenancy: Tenancy) -> OrganizationRead:
    org = await tenancy.get_org(user)
    return OrganizationRead.model_validate(org)


@router.get(
    "/projects",
    response_model=list[ProjectRead],
    summary="List my organization's projects",
)
async def list_projects(user: CurrentUser, tenancy: Tenancy) -> list[ProjectRead]:
    projects = await tenancy.list_projects(user)
    return [ProjectRead.model_validate(p) for p in projects]


@router.post(
    "/projects",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    data: ProjectCreate, user: CurrentUser, tenancy: Tenancy
) -> ProjectRead:
    try:
        project = await tenancy.create_project(user, data.name)
    except ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail) from None
    return ProjectRead.model_validate(project)


@router.get(
    "/projects/{project_id}",
    response_model=ProjectRead,
    summary="Get a project",
)
async def get_project(
    project_id: uuid.UUID, user: CurrentUser, tenancy: Tenancy
) -> ProjectRead:
    try:
        project = await tenancy.get_project(user, project_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.detail) from None
    return ProjectRead.model_validate(project)
