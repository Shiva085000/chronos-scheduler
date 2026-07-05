"""Organization and project management."""

import uuid

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, Project, User
from app.repositories.tenancy import TenancyRepository
from app.services.exceptions import ConflictError, NotFoundError

logger = structlog.get_logger(__name__)


class TenancyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TenancyRepository(session)

    async def get_org(self, user: User) -> Organization:
        org = await self.repo.get_org(user.org_id)
        if org is None:  # pragma: no cover — FK guarantees existence
            raise NotFoundError("organization not found")
        return org

    async def list_projects(self, user: User) -> list[Project]:
        return await self.repo.list_projects(user.org_id)

    async def get_project(self, user: User, project_id: uuid.UUID) -> Project:
        project = await self.repo.get_project(project_id, user.org_id)
        if project is None:
            raise NotFoundError("project not found")
        return project

    async def create_project(self, user: User, name: str) -> Project:
        project = self.repo.add_project(Project(org_id=user.org_id, name=name))
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                f"a project named {name!r} already exists in your organization"
            ) from None
        logger.info("project.created", project_id=str(project.id), name=name)
        return project
