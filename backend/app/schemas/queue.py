import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.retry import RetryStrategy


class QueueCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_id: uuid.UUID | None = Field(
        default=None, description="Omit to use the org's default project."
    )
    shard_key: int = Field(
        default=0,
        ge=0,
        le=255,
        description="Logical shard; workers subscribe via WORKER_SHARD.",
    )
    max_concurrency: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        description="Fleet-wide cap on concurrently RUNNING jobs; null = unlimited.",
    )
    default_priority: int = Field(default=0, ge=-100, le=100)
    default_max_attempts: int = Field(default=3, ge=1, le=20)
    default_backoff_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    default_backoff_base_seconds: int = Field(default=5, ge=0, le=3600)
    default_backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    default_backoff_max_seconds: int = Field(default=300, ge=1, le=86400)
    default_timeout_seconds: int = Field(default=300, ge=1, le=86400)


class QueueUpdate(BaseModel):
    """Partial update; only fields present in the request are applied.

    Editing defaults affects future jobs only — jobs denormalize their
    policy at enqueue time.
    """

    paused: bool | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=10_000)
    default_priority: int | None = Field(default=None, ge=-100, le=100)
    default_max_attempts: int | None = Field(default=None, ge=1, le=20)
    default_backoff_strategy: RetryStrategy | None = None
    default_backoff_base_seconds: int | None = Field(default=None, ge=0, le=3600)
    default_backoff_factor: float | None = Field(default=None, ge=1.0, le=10.0)
    default_backoff_max_seconds: int | None = Field(default=None, ge=1, le=86400)
    default_timeout_seconds: int | None = Field(default=None, ge=1, le=86400)
    shard_key: int | None = Field(default=None, ge=0, le=255)


class QueueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    paused: bool
    shard_key: int
    max_concurrency: int | None
    default_priority: int
    default_max_attempts: int
    default_backoff_strategy: RetryStrategy
    default_backoff_base_seconds: int
    default_backoff_factor: float
    default_backoff_max_seconds: int
    default_timeout_seconds: int
    created_at: dt.datetime
    updated_at: dt.datetime


class QueueStats(BaseModel):
    queue_id: uuid.UUID
    name: str
    paused: bool
    max_concurrency: int | None
    counts_by_status: dict[str, int]


class QueueWithStats(QueueRead):
    counts_by_status: dict[str, int]
