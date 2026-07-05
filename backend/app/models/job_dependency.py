"""Workflow dependencies: edges in a job DAG.

A row (job_id, depends_on_job_id) means ``job_id`` must not be claimed
until ``depends_on_job_id`` reaches SUCCEEDED.  The claim query excludes
blocked jobs via a NOT EXISTS subquery, so the cost is only paid by jobs
that actually participate in workflows.

Cycles are prevented at the API layer (topological validation on create);
the database enforces referential integrity and uniqueness only.
"""

import uuid

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobDependency(Base):
    __tablename__ = "job_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("job_id", "depends_on_job_id", name="uq_job_dep_pair"),
        # Reverse lookup: when job X succeeds, find all jobs waiting on it.
        Index("ix_job_deps_depends_on", "depends_on_job_id"),
    )
