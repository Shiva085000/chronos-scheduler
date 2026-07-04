import datetime as dt
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.attempt import AttemptStatus
from app.models.job import JobStatus

# Payloads are inputs, not blobs: large data belongs in object storage with
# a reference in the payload. The cap keeps rows TOAST-friendly and closes
# a memory-DoS vector on the API.
MAX_PAYLOAD_BYTES = 64 * 1024


class JobCreate(BaseModel):
    task_name: str = Field(min_length=1, max_length=255, examples=["demo.sleep"])
    payload: dict[str, Any] = Field(default_factory=dict)
    queue: str = Field(default="default", min_length=1, max_length=100)
    priority: int = Field(default=0, ge=-100, le=100)
    run_at: dt.datetime | None = Field(
        default=None, description="Schedule for the future; omit to run ASAP"
    )
    max_attempts: int = Field(default=3, ge=1, le=20)
    timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        description=(
            "Wall-clock budget for one execution attempt. A timeout counts "
            "as a normal failure: it consumes the attempt and follows the "
            "retry/DLQ policy."
        ),
    )
    backoff_base_seconds: int = Field(default=5, ge=0, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    backoff_max_seconds: int = Field(default=300, ge=1, le=86400)
    idempotency_key: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Deduplication key, unique per user. Resubmitting with the same "
            "key returns the existing job instead of enqueuing a duplicate. "
            "May also be supplied via the Idempotency-Key header."
        ),
    )

    @field_validator("payload")
    @classmethod
    def _payload_within_limit(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"payload is {size} bytes; the limit is {MAX_PAYLOAD_BYTES} "
                "(64KB). Store large data externally and reference it."
            )
        return value


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    queue: str
    task_name: str
    payload: dict[str, Any]
    status: JobStatus
    priority: int
    run_at: dt.datetime
    idempotency_key: str | None
    max_attempts: int
    attempt_count: int
    timeout_seconds: int
    backoff_base_seconds: int
    backoff_factor: float
    backoff_max_seconds: int
    locked_by: uuid.UUID | None
    lease_expires_at: dt.datetime | None
    last_error: str | None
    result: dict[str, Any] | None
    created_at: dt.datetime
    updated_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class AttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    worker_id: uuid.UUID | None
    attempt_number: int
    status: AttemptStatus
    error: str | None
    started_at: dt.datetime
    finished_at: dt.datetime | None
