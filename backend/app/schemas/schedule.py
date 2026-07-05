import datetime as dt
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.cron import InvalidCronExpression, validate_cron
from app.domain.retry import RetryStrategy
from app.schemas.job import MAX_PAYLOAD_BYTES


def _check_cron(value: str) -> str:
    try:
        return validate_cron(value)
    except InvalidCronExpression as exc:
        raise ValueError(str(exc)) from None


class ScheduleCreate(BaseModel):
    task_name: str = Field(min_length=1, max_length=255, examples=["demo.echo"])
    payload: dict[str, Any] = Field(default_factory=dict)
    cron_expr: str = Field(
        min_length=1,
        max_length=100,
        examples=["*/5 * * * *"],
        description="Standard 5-field cron, evaluated in UTC.",
    )
    queue: str = Field(default="default", min_length=1, max_length=100)
    project_id: uuid.UUID | None = Field(
        default=None, description="Omit to use the org's default project."
    )
    priority: int = Field(default=0, ge=-100, le=100)
    max_attempts: int = Field(default=3, ge=1, le=20)
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    backoff_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    backoff_base_seconds: int = Field(default=5, ge=0, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    backoff_max_seconds: int = Field(default=300, ge=1, le=86400)

    @field_validator("cron_expr")
    @classmethod
    def _cron_valid(cls, value: str) -> str:
        return _check_cron(value)

    @field_validator("payload")
    @classmethod
    def _payload_within_limit(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"payload is {size} bytes; the limit is {MAX_PAYLOAD_BYTES} (64KB)."
            )
        return value


class ScheduleUpdate(BaseModel):
    """Partial update; changing cron_expr recomputes next_run_at."""

    cron_expr: str | None = Field(default=None, min_length=1, max_length=100)
    paused: bool | None = None
    payload: dict[str, Any] | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86400)

    @field_validator("cron_expr")
    @classmethod
    def _cron_valid(cls, value: str | None) -> str | None:
        return None if value is None else _check_cron(value)


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    queue_id: uuid.UUID
    queue: str
    task_name: str
    payload: dict[str, Any]
    cron_expr: str
    paused: bool
    priority: int
    max_attempts: int
    timeout_seconds: int
    backoff_strategy: RetryStrategy
    backoff_base_seconds: int
    backoff_factor: float
    backoff_max_seconds: int
    next_run_at: dt.datetime
    last_run_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
