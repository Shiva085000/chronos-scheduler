"""initial schema: users, workers, jobs, job_attempts

Revision ID: 0001
Revises:
Create Date: 2026-07-04

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

job_status = postgresql.ENUM(
    "pending", "running", "succeeded", "cancelled", "dead",
    name="job_status", create_type=False,
)
worker_status = postgresql.ENUM(
    "online", "draining", "offline",
    name="worker_status", create_type=False,
)
attempt_status = postgresql.ENUM(
    "running", "succeeded", "failed", "lost", "aborted",
    name="attempt_status", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    job_status.create(bind, checkfirst=True)
    worker_status.create(bind, checkfirst=True)
    attempt_status.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "workers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", worker_status, nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workers_status", "workers", ["status"])

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_jobs_owner_id_users"),
            nullable=False,
        ),
        sa.Column("queue", sa.String(100), nullable=False),
        sa.Column("task_name", sa.String(255), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", job_status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("backoff_base_seconds", sa.Integer(), nullable=False),
        sa.Column("backoff_factor", sa.Float(), nullable=False),
        sa.Column("backoff_max_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "locked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workers.id", ondelete="SET NULL", name="fk_jobs_locked_by_workers"
            ),
            nullable=True,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 20", name="ck_jobs_max_attempts_range"
        ),
        sa.CheckConstraint(
            "priority BETWEEN -100 AND 100", name="ck_jobs_priority_range"
        ),
        sa.CheckConstraint(
            "backoff_base_seconds BETWEEN 0 AND 3600",
            name="ck_jobs_backoff_base_range",
        ),
        sa.CheckConstraint(
            "backoff_factor >= 1.0 AND backoff_factor <= 10.0",
            name="ck_jobs_backoff_factor_range",
        ),
        sa.CheckConstraint(
            "backoff_max_seconds BETWEEN 1 AND 86400",
            name="ck_jobs_backoff_max_range",
        ),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index(
        "ix_jobs_claim",
        "jobs",
        [sa.text("priority DESC"), "run_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_jobs_lease",
        "jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_jobs_owner_idempotency",
        "jobs",
        ["owner_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_jobs_owner_created", "jobs", ["owner_id", sa.text("created_at DESC")]
    )

    op.create_table(
        "job_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "jobs.id", ondelete="CASCADE", name="fk_job_attempts_job_id_jobs"
            ),
            nullable=False,
        ),
        sa.Column(
            "worker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workers.id",
                ondelete="SET NULL",
                name="fk_job_attempts_worker_id_workers",
            ),
            nullable=True,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", attempt_status, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_job_attempts_job_number", "job_attempts", ["job_id", "attempt_number"]
    )
    op.create_index("ix_job_attempts_finished", "job_attempts", ["finished_at"])


def downgrade() -> None:
    op.drop_table("job_attempts")
    op.drop_table("jobs")
    op.drop_table("workers")
    op.drop_table("users")
    bind = op.get_bind()
    attempt_status.drop(bind, checkfirst=True)
    job_status.drop(bind, checkfirst=True)
    worker_status.drop(bind, checkfirst=True)
