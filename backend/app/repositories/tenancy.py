"""Organization and project data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, Project


class TenancyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add_org(self, org: Organization) -> Organization:
        self.session.add(org)
        return org

    def add_project(self, project: Project) -> Project:
        self.session.add(project)
        return project

    async def get_org(self, org_id: uuid.UUID) -> Organization | None:
        return await self.session.get(Organization, org_id)

    async def get_project(
        self, project_id: uuid.UUID, org_id: uuid.UUID
    ) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.id == project_id, Project.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def list_projects(self, org_id: uuid.UUID) -> list[Project]:
        result = await self.session.execute(
            select(Project)
            .where(Project.org_id == org_id)
            .order_by(Project.created_at.asc())
        )
        return list(result.scalars())

    async def default_project(self, org_id: uuid.UUID) -> Project | None:
        """The org's oldest project — the one auto-created at registration.

        Deterministic fallback for requests that don't name a project.
        """
        result = await self.session.execute(
            select(Project)
            .where(Project.org_id == org_id)
            .order_by(Project.created_at.asc(), Project.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()
