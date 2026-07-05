"""Integration tests: the cron materializer.

Requires a running Postgres (set TEST_DATABASE_URL); skipped otherwise.

    docker compose exec api sh -c \
      "TEST_DATABASE_URL=$DATABASE_URL python -m pytest tests/test_schedule_materialization.py -v"

Invariants under test:
- a due schedule materializes exactly one job and advances its cursor;
- an already-advanced schedule does nothing on the next sweep;
- a pile of missed ticks collapses into a single firing (no flood);
- the materialized job carries the schedule's retry policy and the
  deterministic idempotency key.
"""

import datetime as dt
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


async def _db_now(session):
    from sqlalchemy import text

    return (await session.execute(text("SELECT now()"))).scalar_one()


async def test_due_schedule_fires_once_and_advances(session_factory):
    from sqlalchemy import delete, select

    from app.domain.retry import RetryStrategy
    from app.models import Job, Organization, Schedule
    from app.services.execution_service import ExecutionService
    from tests.factories import create_owner_with_queue

    queue_name = f"schedtest-{uuid.uuid4()}"
    async with session_factory() as session:
        org, user, queue = await create_owner_with_queue(session, queue_name)
        now = await _db_now(session)
        # An hour of missed every-minute ticks: must yield ONE job, not 60.
        overdue = now - dt.timedelta(hours=1)
        schedule = Schedule(
            owner_id=user.id,
            queue_id=queue.id,
            queue=queue_name,
            task_name="demo.echo",
            payload={"message": "tick"},
            cron_expr="* * * * *",
            max_attempts=7,
            backoff_strategy=RetryStrategy.LINEAR,
            next_run_at=overdue,
        )
        session.add(schedule)
        await session.commit()
        org_id, schedule_id = org.id, schedule.id

    executor = ExecutionService(session_factory)
    try:
        created = await executor.materialize_schedules_once()
        assert created == 1, f"expected 1 materialized job, got {created}"

        async with session_factory() as session:
            jobs = (
                (
                    await session.execute(
                        select(Job).where(Job.queue == queue_name)
                    )
                )
                .scalars()
                .all()
            )
            assert len(jobs) == 1, "missed ticks must collapse into one firing"
            job = jobs[0]
            assert job.task_name == "demo.echo"
            assert job.max_attempts == 7, "job must inherit the schedule's policy"
            assert job.backoff_strategy == RetryStrategy.LINEAR
            assert job.idempotency_key == f"schedule:{schedule_id}:{overdue.isoformat()}"

            refreshed = await session.get(Schedule, schedule_id)
            now = await _db_now(session)
            assert refreshed.next_run_at > now, "cursor must land in the future"
            assert refreshed.last_run_at == overdue

        # The cursor moved past `now`, so a second sweep is a no-op.
        assert await executor.materialize_schedules_once() == 0
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Organization).where(Organization.id == org_id)
            )
            await session.commit()


async def test_paused_schedule_never_fires(session_factory):
    from sqlalchemy import delete, select

    from app.models import Job, Organization, Schedule
    from app.services.execution_service import ExecutionService
    from tests.factories import create_owner_with_queue

    queue_name = f"schedpaused-{uuid.uuid4()}"
    async with session_factory() as session:
        org, user, queue = await create_owner_with_queue(session, queue_name)
        now = await _db_now(session)
        session.add(
            Schedule(
                owner_id=user.id,
                queue_id=queue.id,
                queue=queue_name,
                task_name="demo.echo",
                cron_expr="* * * * *",
                paused=True,
                next_run_at=now - dt.timedelta(minutes=5),
            )
        )
        await session.commit()
        org_id = org.id

    try:
        await ExecutionService(session_factory).materialize_schedules_once()
        async with session_factory() as session:
            jobs = (
                (await session.execute(select(Job).where(Job.queue == queue_name)))
                .scalars()
                .all()
            )
            assert jobs == [], "a paused schedule materialized a job"
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Organization).where(Organization.id == org_id)
            )
            await session.commit()
