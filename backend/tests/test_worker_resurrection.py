"""A heartbeating worker falsely declared dead must self-heal.

Scenario: the reaper marks a worker OFFLINE during a transient stall (host
sleep, paused container). When heartbeats resume, the worker's status must
return to ONLINE and stopped_at must clear — otherwise the fleet view and
workers_online stat stay wrong forever. DRAINING must NOT be overwritten.

Requires Postgres (TEST_DATABASE_URL); skipped otherwise.
"""

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)


@pytest.fixture
async def session_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_worker(session_factory, status):
    from sqlalchemy import func

    from app.models import Worker

    async with session_factory() as session:
        worker = Worker(
            id=uuid.uuid4(),
            name=f"resurrection-test-{uuid.uuid4()}",
            status=status,
            concurrency=1,
            stopped_at=func.now(),
        )
        session.add(worker)
        await session.commit()
        return worker.id


async def _cleanup(session_factory, worker_id):
    from sqlalchemy import delete

    from app.models import Worker

    async with session_factory() as session:
        await session.execute(delete(Worker).where(Worker.id == worker_id))
        await session.commit()


async def test_heartbeat_resurrects_falsely_dead_worker(session_factory):
    from app.models import Worker, WorkerStatus
    from app.services.execution_service import ExecutionService

    worker_id = await _make_worker(session_factory, WorkerStatus.OFFLINE)
    try:
        await ExecutionService(session_factory).heartbeat(worker_id, 30)

        async with session_factory() as session:
            worker = await session.get(Worker, worker_id)
            assert worker.status == WorkerStatus.ONLINE
            assert worker.stopped_at is None
    finally:
        await _cleanup(session_factory, worker_id)


async def test_heartbeat_does_not_disturb_draining_worker(session_factory):
    from app.models import Worker, WorkerStatus
    from app.services.execution_service import ExecutionService

    worker_id = await _make_worker(session_factory, WorkerStatus.DRAINING)
    try:
        await ExecutionService(session_factory).heartbeat(worker_id, 30)

        async with session_factory() as session:
            worker = await session.get(Worker, worker_id)
            assert worker.status == WorkerStatus.DRAINING
    finally:
        await _cleanup(session_factory, worker_id)
