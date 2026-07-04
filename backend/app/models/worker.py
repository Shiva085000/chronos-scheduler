import datetime as dt
import enum
import uuid

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkerStatus(str, enum.Enum):
    """Worker lifecycle.

    ONLINE   -> heartbeating and claiming jobs
    DRAINING -> received shutdown signal; finishing in-flight jobs, not claiming
    OFFLINE  -> exited cleanly, or declared dead by the reaper after missing
                heartbeats for `worker_offline_after_seconds`
    """

    ONLINE = "online"
    DRAINING = "draining"
    OFFLINE = "offline"


worker_status_enum = Enum(
    WorkerStatus,
    name="worker_status",
    values_callable=lambda e: [m.value for m in e],
)


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[WorkerStatus] = mapped_column(
        worker_status_enum, nullable=False, default=WorkerStatus.ONLINE
    )
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_heartbeat_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    stopped_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
