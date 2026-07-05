"""Workflow dependency data access.

Reads and writes to the ``job_dependencies`` junction table. The critical
query — ``are_all_met`` — is a single EXISTS scan that the claim CTE
uses to filter out blocked jobs without materializing the dependency set.
"""

import uuid

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobDependency, JobStatus
from app.models.job import Job


class DependencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, job_id: uuid.UUID, depends_on_job_id: uuid.UUID) -> JobDependency:
        dep = JobDependency(job_id=job_id, depends_on_job_id=depends_on_job_id)
        self.session.add(dep)
        return dep

    async def add_many(
        self, job_id: uuid.UUID, depends_on_ids: list[uuid.UUID]
    ) -> list[JobDependency]:
        deps = []
        for dep_id in depends_on_ids:
            d = JobDependency(job_id=job_id, depends_on_job_id=dep_id)
            self.session.add(d)
            deps.append(d)
        return deps

    async def get_for_job(self, job_id: uuid.UUID) -> list[JobDependency]:
        result = await self.session.execute(
            select(JobDependency).where(JobDependency.job_id == job_id)
        )
        return list(result.scalars().all())

    async def are_all_met(self, job_id: uuid.UUID) -> bool:
        """True if every upstream job has status = SUCCEEDED."""
        unmet = await self.session.execute(
            select(
                exists(
                    select(JobDependency.id)
                    .join(Job, Job.id == JobDependency.depends_on_job_id)
                    .where(
                        JobDependency.job_id == job_id,
                        Job.status != JobStatus.SUCCEEDED,
                    )
                )
            )
        )
        return not unmet.scalar()

    async def get_dependents(self, completed_job_id: uuid.UUID) -> list[uuid.UUID]:
        """Find all job_ids that depend on the given (just-completed) job."""
        result = await self.session.execute(
            select(JobDependency.job_id).where(
                JobDependency.depends_on_job_id == completed_job_id
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def unmet_deps_subquery():
        """A NOT EXISTS clause for use in the claim CTE — excludes jobs
        that have at least one dependency that is not yet SUCCEEDED.

        Returns a column expression suitable for ``.where(expr)``."""
        return ~exists(
            select(JobDependency.id)
            .join(Job, Job.id == JobDependency.depends_on_job_id)
            .where(
                JobDependency.job_id == Job.id,  # correlate outer
                Job.status != JobStatus.SUCCEEDED,
            )
            .correlate(Job)
        )
