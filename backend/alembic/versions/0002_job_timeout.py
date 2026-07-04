"""add per-job execution timeout

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-04

"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default backfills existing rows with the 300s default; kept in
    # place afterwards so raw inserts get a sane value too.
    op.add_column(
        "jobs",
        sa.Column(
            "timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
    )
    op.create_check_constraint(
        "ck_jobs_timeout_range", "jobs", "timeout_seconds BETWEEN 1 AND 86400"
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_timeout_range", "jobs", type_="check")
    op.drop_column("jobs", "timeout_seconds")
