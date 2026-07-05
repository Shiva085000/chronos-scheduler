"""Recurring (cron) schedules.

A schedule is a template plus a cursor: `next_run_at` is the single
source of truth for "when does this fire next". The materializer (one
sweep cluster-wide, advisory-locked like the reaper) turns due schedules
into ordinary `jobs` rows — everything downstream (claiming, retries,
DLQ, metrics) treats a cron-born job identically to a user-enqueued one.

Two protocol decisions worth naming:
- **Exactly-once firing** rides on the jobs idempotency index: each
  materialized job carries idempotency_key "schedule:<id>:<fire time>",
  so even a crash between insert and cursor update cannot double-fire.
- **No catch-up flood**: if the scheduler was down across N missed
  ticks, the schedule fires once and the cursor jumps to the next future
  tick. A monitoring cron that missed an hour wants one run now, not 60.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.retry import RetryStrategy
from app.models.queue import retry_strategy_enum


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("queues.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized queue name, mirroring jobs.queue (workers consume by name).
    queue: Mapped[str] = mapped_column(String(100), nullable=False)

    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Standard 5-field cron, evaluated in UTC (see app.domain.cron).
    cron_expr: Mapped[str] = mapped_column(String(100), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- template for the jobs this schedule materializes ------------------
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    backoff_strategy: Mapped[RetryStrategy] = mapped_column(
        retry_strategy_enum, nullable=False, default=RetryStrategy.EXPONENTIAL
    )
    backoff_base_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    backoff_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    backoff_max_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)

    # --- cursor -------------------------------------------------------------
    next_run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_run_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # The materializer's scan: due, unpaused schedules only.
        Index(
            "ix_schedules_due",
            "next_run_at",
            postgresql_where=text("NOT paused"),
        ),
        Index("ix_schedules_owner", "owner_id"),
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
