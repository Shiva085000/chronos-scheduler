"""bonus features: rbac, workflow deps, queue sharding, ai summaries

Revision ID: 0004
Revises: 0003_tenancy_queues_schedules
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- RBAC: user role enum + column --------------------------------
    user_role_enum = sa.Enum(
        "owner", "admin", "member", "viewer", name="user_role"
    )
    user_role_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "users",
        sa.Column("role", user_role_enum, nullable=False, server_default="owner"),
    )

    # ---- Workflow dependencies ----------------------------------------
    op.add_column(
        "jobs",
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_jobs_workflow",
        "jobs",
        ["workflow_id"],
        postgresql_where=sa.text("workflow_id IS NOT NULL"),
    )

    op.create_table(
        "job_dependencies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "depends_on_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("job_id", "depends_on_job_id", name="uq_job_dep_pair"),
    )
    op.create_index("ix_job_deps_depends_on", "job_dependencies", ["depends_on_job_id"])

    # ---- Queue sharding ------------------------------------------------
    op.add_column(
        "queues",
        sa.Column("shard_key", sa.Integer(), nullable=False, server_default="0"),
    )

    # ---- AI failure summaries ------------------------------------------
    op.add_column(
        "jobs",
        sa.Column("ai_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "ai_summary")
    op.drop_column("queues", "shard_key")
    op.drop_index("ix_job_deps_depends_on", table_name="job_dependencies")
    op.drop_table("job_dependencies")
    op.drop_index("ix_jobs_workflow", table_name="jobs")
    op.drop_column("jobs", "workflow_id")
    op.drop_column("users", "role")
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
