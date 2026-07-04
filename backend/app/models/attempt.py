import datetime as dt
import enum
import uuid

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AttemptStatus(str, enum.Enum):
    """Outcome of a single execution attempt.

    RUNNING   -> in flight
    SUCCEEDED -> handler returned normally
    FAILED    -> handler raised; retry/DLQ decision recorded on the job
    LOST      -> lease expired; the reaper presumed the worker dead
    ABORTED   -> worker shut down gracefully mid-run and released the job;
                 does NOT count against max_attempts
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"
    ABORTED = "aborted"


attempt_status_enum = Enum(
    AttemptStatus,
    name="attempt_status",
    values_callable=lambda e: [m.value for m in e],
)


class JobAttempt(Base):
    """Append-only audit log of executions. One row per claim.

    Kept separate from `jobs` so the job row stays a compact state-machine
    record while full execution history (which worker, when, what error)
    remains queryable for debugging and throughput metrics.
    """

    __tablename__ = "job_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workers.id", ondelete="SET NULL"),
        nullable=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AttemptStatus] = mapped_column(
        attempt_status_enum, nullable=False, default=AttemptStatus.RUNNING
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Non-unique: a DLQ requeue resets attempt_count, so attempt numbers
        # restart. Old rows are terminal, so lookups guarded by
        # status = 'running' remain unambiguous.
        Index("ix_job_attempts_job_number", "job_id", "attempt_number"),
        # Throughput chart: recent finished attempts grouped by minute.
        Index("ix_job_attempts_finished", "finished_at"),
    )
