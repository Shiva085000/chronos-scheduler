import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.models.worker import WorkerStatus


class WorkerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: WorkerStatus
    concurrency: int
    last_heartbeat_at: dt.datetime
    started_at: dt.datetime
    stopped_at: dt.datetime | None
