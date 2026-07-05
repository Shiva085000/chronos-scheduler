"""tenancy (organizations, projects), queues as entities, cron schedules,
batch ids, and named retry strategies

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-04

Backfill strategy for pre-existing data (idempotent, pure SQL):
1. every existing user gets a personal organization named after their
   email and a project named "default" inside it;
2. every distinct (owner, queue-name) pair found in jobs becomes a queue
   row in that owner's default project;
3. jobs.queue_id is backfilled from that mapping, then made NOT NULL.
A fresh database runs the same code over zero rows.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

retry_strategy = postgresql.ENUM(
    "fixed", "linear", "exponential",
    name="retry_strategy", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    retry_strategy.create(bind, checkfirst=True)

    # --- tenancy ----------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "organizations.id",
                ondelete="CASCADE",
                name="fk_projects_org_id_organizations",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_projects_org_name"),
    )

    # --- queues -------------------------------------------------------------
    op.create_table(
        "queues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "projects.id", ondelete="CASCADE", name="fk_queues_project_id_projects"
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_concurrency", sa.Integer(), nullable=True),
        sa.Column("default_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "default_max_attempts", sa.Integer(), nullable=False, server_default="3"
        ),
        sa.Column(
            "default_backoff_strategy",
            retry_strategy,
            nullable=False,
            server_default="exponential",
        ),
        sa.Column(
            "default_backoff_base_seconds",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
        sa.Column(
            "default_backoff_factor", sa.Float(), nullable=False, server_default="2.0"
        ),
        sa.Column(
            "default_backoff_max_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
        sa.Column(
            "default_timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
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
        sa.UniqueConstraint("project_id", "name", name="uq_queues_project_name"),
        sa.CheckConstraint(
            "max_concurrency IS NULL OR max_concurrency BETWEEN 1 AND 10000",
            name="ck_queues_max_concurrency_range",
        ),
        sa.CheckConstraint(
            "default_max_attempts BETWEEN 1 AND 20",
            name="ck_queues_default_max_attempts_range",
        ),
        sa.CheckConstraint(
            "default_priority BETWEEN -100 AND 100",
            name="ck_queues_default_priority_range",
        ),
        sa.CheckConstraint(
            "default_backoff_base_seconds BETWEEN 0 AND 3600",
            name="ck_queues_default_backoff_base_range",
        ),
        sa.CheckConstraint(
            "default_backoff_factor >= 1.0 AND default_backoff_factor <= 10.0",
            name="ck_queues_default_backoff_factor_range",
        ),
        sa.CheckConstraint(
            "default_backoff_max_seconds BETWEEN 1 AND 86400",
            name="ck_queues_default_backoff_max_range",
        ),
        sa.CheckConstraint(
            "default_timeout_seconds BETWEEN 1 AND 86400",
            name="ck_queues_default_timeout_range",
        ),
    )

    # --- users.org_id, backfilled via personal orgs -------------------------
    op.add_column(
        "users",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        WITH new_orgs AS (
            INSERT INTO organizations (id, name)
            SELECT gen_random_uuid(), u.email
            FROM users u
            WHERE u.org_id IS NULL
            RETURNING id, name
        )
        UPDATE users u SET org_id = o.id
        FROM new_orgs o
        WHERE o.name = u.email AND u.org_id IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO projects (id, org_id, name)
        SELECT gen_random_uuid(), o.id, 'default'
        FROM organizations o
        WHERE NOT EXISTS (
            SELECT 1 FROM projects p WHERE p.org_id = o.id AND p.name = 'default'
        )
        """
    )
    op.alter_column("users", "org_id", nullable=False)
    op.create_foreign_key(
        "fk_users_org_id_organizations",
        "users",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # --- jobs: queue_id / batch_id / backoff_strategy ------------------------
    op.add_column(
        "jobs",
        sa.Column("queue_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "backoff_strategy",
            retry_strategy,
            nullable=False,
            server_default="exponential",
        ),
    )
    # Materialize a queue row for every queue name already in use, inside
    # the job owner's default project, then point the jobs at them.
    op.execute(
        """
        INSERT INTO queues (id, project_id, name)
        SELECT gen_random_uuid(), p.id, j.queue
        FROM (SELECT DISTINCT owner_id, queue FROM jobs) j
        JOIN users u ON u.id = j.owner_id
        JOIN projects p ON p.org_id = u.org_id AND p.name = 'default'
        ON CONFLICT (project_id, name) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE jobs SET queue_id = q.id
        FROM users u, projects p, queues q
        WHERE u.id = jobs.owner_id
          AND p.org_id = u.org_id AND p.name = 'default'
          AND q.project_id = p.id AND q.name = jobs.queue
          AND jobs.queue_id IS NULL
        """
    )
    op.alter_column("jobs", "queue_id", nullable=False)
    op.create_foreign_key(
        "fk_jobs_queue_id_queues",
        "jobs",
        "queues",
        ["queue_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_jobs_queue_running",
        "jobs",
        ["queue_id"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_jobs_batch",
        "jobs",
        ["batch_id"],
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )

    # --- schedules ------------------------------------------------------------
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="CASCADE", name="fk_schedules_owner_id_users"
            ),
            nullable=False,
        ),
        sa.Column(
            "queue_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "queues.id", ondelete="CASCADE", name="fk_schedules_queue_id_queues"
            ),
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
        sa.Column("cron_expr", sa.String(100), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "timeout_seconds", sa.Integer(), nullable=False, server_default="300"
        ),
        sa.Column(
            "backoff_strategy",
            retry_strategy,
            nullable=False,
            server_default="exponential",
        ),
        sa.Column(
            "backoff_base_seconds", sa.Integer(), nullable=False, server_default="5"
        ),
        sa.Column("backoff_factor", sa.Float(), nullable=False, server_default="2.0"),
        sa.Column(
            "backoff_max_seconds", sa.Integer(), nullable=False, server_default="300"
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 20", name="ck_schedules_max_attempts_range"
        ),
        sa.CheckConstraint(
            "priority BETWEEN -100 AND 100", name="ck_schedules_priority_range"
        ),
        sa.CheckConstraint(
            "backoff_base_seconds BETWEEN 0 AND 3600",
            name="ck_schedules_backoff_base_range",
        ),
        sa.CheckConstraint(
            "backoff_factor >= 1.0 AND backoff_factor <= 10.0",
            name="ck_schedules_backoff_factor_range",
        ),
        sa.CheckConstraint(
            "backoff_max_seconds BETWEEN 1 AND 86400",
            name="ck_schedules_backoff_max_range",
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 86400",
            name="ck_schedules_timeout_range",
        ),
    )
    op.create_index(
        "ix_schedules_due",
        "schedules",
        ["next_run_at"],
        postgresql_where=sa.text("NOT paused"),
    )
    op.create_index("ix_schedules_owner", "schedules", ["owner_id"])


def downgrade() -> None:
    op.drop_table("schedules")
    op.drop_index("ix_jobs_batch", table_name="jobs")
    op.drop_index("ix_jobs_queue_running", table_name="jobs")
    op.drop_constraint("fk_jobs_queue_id_queues", "jobs", type_="foreignkey")
    op.drop_column("jobs", "backoff_strategy")
    op.drop_column("jobs", "batch_id")
    op.drop_column("jobs", "queue_id")
    op.drop_constraint("fk_users_org_id_organizations", "users", type_="foreignkey")
    op.drop_column("users", "org_id")
    op.drop_table("queues")
    op.drop_table("projects")
    op.drop_table("organizations")
    retry_strategy.drop(op.get_bind(), checkfirst=True)
