import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobStatus(str, enum.Enum):
    """Job lifecycle state machine.

    PENDING   -> waiting to run (includes jobs scheduled for the future and
                 jobs waiting out a retry backoff; attempt_count > 0 means
                 the job is retrying)
    RUNNING   -> claimed by a worker holding a live lease
    SUCCEEDED -> terminal success
    CANCELLED -> terminal; cancelled by the owner while still pending
    DEAD      -> terminal failure after exhausting max_attempts; the set of
                 DEAD jobs *is* the dead letter queue

    Allowed transitions (enforced by guarded UPDATEs, never blind writes):
      PENDING   -> RUNNING (atomic claim), CANCELLED
      RUNNING   -> SUCCEEDED, PENDING (retry / released on shutdown), DEAD
      DEAD      -> PENDING (manual DLQ requeue)
      CANCELLED -> PENDING (manual requeue)
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    DEAD = "dead"


job_status_enum = Enum(
    JobStatus,
    name="job_status",
    values_callable=lambda e: [m.value for m in e],
)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    queue: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    status: Mapped[JobStatus] = mapped_column(
        job_status_enum, nullable=False, default=JobStatus.PENDING, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Client-supplied deduplication key; unique per owner (partial index below).
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- retry policy (denormalized onto the job so a policy change never
    # --- retroactively alters in-flight jobs) ---------------------------
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Wall-clock budget for a single execution attempt, enforced by the
    # worker via asyncio.wait_for. A timeout is a normal failure: it burns
    # the attempt and follows the retry/DLQ policy unchanged.
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    backoff_base_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    backoff_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    backoff_max_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)

    # --- lease (set while RUNNING, cleared on every transition out) ------
    locked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workers.id", ondelete="SET NULL"),
        nullable=True,
    )
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Hot path: the claim query scans only ready PENDING rows in claim
        # order. A partial index stays tiny no matter how many terminal
        # jobs accumulate.
        Index(
            "ix_jobs_claim",
            text("priority DESC"),
            "run_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # Reaper scan: only RUNNING rows carry leases.
        Index(
            "ix_jobs_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        # Idempotent enqueue: at most one job per (owner, key).
        Index(
            "uq_jobs_owner_idempotency",
            "owner_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Owner-scoped listing, newest first.
        Index("ix_jobs_owner_created", "owner_id", text("created_at DESC")),
        CheckConstraint("max_attempts BETWEEN 1 AND 20", name="max_attempts_range"),
        CheckConstraint("priority BETWEEN -100 AND 100", name="priority_range"),
        CheckConstraint(
            "backoff_base_seconds BETWEEN 0 AND 3600", name="backoff_base_range"
        ),
        CheckConstraint(
            "backoff_factor >= 1.0 AND backoff_factor <= 10.0",
            name="backoff_factor_range",
        ),
        CheckConstraint(
            "backoff_max_seconds BETWEEN 1 AND 86400", name="backoff_max_range"
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 86400", name="timeout_range"
        ),
    )
