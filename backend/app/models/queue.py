"""Queues as first-class configuration objects.

A queue row carries operator controls (pause, concurrency cap) and the
default retry policy for jobs enqueued into it. Jobs still *denormalize*
their effective policy at enqueue time (see Job), so editing a queue's
defaults never retroactively changes in-flight jobs — the queue is a
template, the job is the contract.

`paused` and `max_concurrency` are enforced in the claim path:
- paused queues are simply excluded from the candidate scan (running
  jobs finish; nothing new starts),
- capped queues are claimed under a FOR UPDATE lock on the queue row, so
  the running-count check and the admission are atomic and the cap can
  never be exceeded, at the cost of serializing claims per capped queue.
Uncapped queues take the lock-free fast path and pay nothing.
"""

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.retry import RetryStrategy

retry_strategy_enum = Enum(
    RetryStrategy,
    name="retry_strategy",
    values_callable=lambda e: [m.value for m in e],
)


class Queue(Base):
    __tablename__ = "queues"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # --- operator controls -----------------------------------------------
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Logical shard assignment. Workers can subscribe to a specific shard
    # via WORKER_SHARD env var; default 0 means "general pool".
    shard_key: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NULL = unlimited. A cap bounds how many of this queue's jobs may be
    # RUNNING fleet-wide (not per worker).
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- defaults applied to jobs that don't specify their own ------------
    default_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    default_max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )
    default_backoff_strategy: Mapped[RetryStrategy] = mapped_column(
        retry_strategy_enum, nullable=False, default=RetryStrategy.EXPONENTIAL
    )
    default_backoff_base_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5
    )
    default_backoff_factor: Mapped[float] = mapped_column(
        Float, nullable=False, default=2.0
    )
    default_backoff_max_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300
    )
    default_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300
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
        UniqueConstraint("project_id", "name", name="uq_queues_project_name"),
        CheckConstraint(
            "max_concurrency IS NULL OR max_concurrency BETWEEN 1 AND 10000",
            name="max_concurrency_range",
        ),
        CheckConstraint(
            "default_max_attempts BETWEEN 1 AND 20",
            name="default_max_attempts_range",
        ),
        CheckConstraint(
            "default_priority BETWEEN -100 AND 100", name="default_priority_range"
        ),
        CheckConstraint(
            "default_backoff_base_seconds BETWEEN 0 AND 3600",
            name="default_backoff_base_range",
        ),
        CheckConstraint(
            "default_backoff_factor >= 1.0 AND default_backoff_factor <= 10.0",
            name="default_backoff_factor_range",
        ),
        CheckConstraint(
            "default_backoff_max_seconds BETWEEN 1 AND 86400",
            name="default_backoff_max_range",
        ),
        CheckConstraint(
            "default_timeout_seconds BETWEEN 1 AND 86400",
            name="default_timeout_range",
        ),
    )
