import datetime as dt

from pydantic import BaseModel


class ThroughputPoint(BaseModel):
    minute: dt.datetime
    succeeded: int
    failed: int


class StatsOverview(BaseModel):
    counts_by_status: dict[str, int]
    ready_now: int
    scheduled_later: int
    workers_online: int
    dlq_size: int
    throughput_last_hour: list[ThroughputPoint]
    generated_at: dt.datetime
