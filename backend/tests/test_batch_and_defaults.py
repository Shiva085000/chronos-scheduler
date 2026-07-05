"""Integration tests: atomic batch enqueue and queue-default inheritance.

Requires a running Postgres (set TEST_DATABASE_URL); skipped otherwise.

    docker compose exec api sh -c \
      "TEST_DATABASE_URL=$DATABASE_URL python -m pytest tests/test_batch_and_defaults.py -v"
"""

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)


class NullBus:
    """JobService only needs publish_wake; tests don't need Redis."""

    async def publish_wake(self) -> None:
        pass


@pytest.fixture
async def session_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_batch_is_atomic_and_shares_batch_id(session_factory):
    from sqlalchemy import delete

    from app.models import JobStatus, Organization
    from app.schemas.job import JobCreate
    from app.services.exceptions import ConflictError
    from app.services.job_service import JobService
    from tests.factories import create_owner_with_queue

    queue_name = f"batchtest-{uuid.uuid4()}"
    async with session_factory() as session:
        org, user, queue = await create_owner_with_queue(session, queue_name)
        await session.commit()
        org_id = org.id

        service = JobService(session, NullBus())
        items = [
            JobCreate(task_name="demo.echo", payload={"n": i}, queue=queue_name)
            for i in range(5)
        ]
        batch_id, jobs = await service.enqueue_batch(user, items)

        assert len(jobs) == 5
        assert all(j.batch_id == batch_id for j in jobs)
        assert all(j.status == JobStatus.PENDING for j in jobs)

        listed, total = await service.list_jobs(user.id, batch_id=batch_id)
        assert total == 5 and len(listed) == 5

    # A duplicate idempotency key inside the batch rejects the whole batch.
    async with session_factory() as session:
        service = JobService(session, NullBus())
        dupes = [
            JobCreate(
                task_name="demo.echo",
                queue=queue_name,
                idempotency_key="same-key",
            )
            for _ in range(2)
        ]
        with pytest.raises(ConflictError):
            await service.enqueue_batch(user, dupes)
        _, total = await service.list_jobs(user.id, queue=queue_name)
        assert total == 5, "a rejected batch must create nothing"

    async with session_factory() as session:
        await session.execute(delete(Organization).where(Organization.id == org_id))
        await session.commit()


async def test_jobs_inherit_queue_defaults_unless_overridden(session_factory):
    from sqlalchemy import delete

    from app.domain.retry import RetryStrategy
    from app.models import Organization
    from app.schemas.job import JobCreate
    from app.services.job_service import JobService
    from tests.factories import create_owner_with_queue

    queue_name = f"defaultstest-{uuid.uuid4()}"
    async with session_factory() as session:
        org, user, queue = await create_owner_with_queue(
            session,
            queue_name,
            default_max_attempts=7,
            default_backoff_strategy=RetryStrategy.FIXED,
            default_backoff_base_seconds=42,
        )
        await session.commit()
        org_id = org.id

        service = JobService(session, NullBus())

        inherited, _ = await service.enqueue(
            user, JobCreate(task_name="demo.echo", queue=queue_name)
        )
        assert inherited.max_attempts == 7
        assert inherited.backoff_strategy == RetryStrategy.FIXED
        assert inherited.backoff_base_seconds == 42

        overridden, _ = await service.enqueue(
            user,
            JobCreate(task_name="demo.echo", queue=queue_name, max_attempts=2),
        )
        assert overridden.max_attempts == 2, "explicit values must win"
        assert overridden.backoff_strategy == RetryStrategy.FIXED

    async with session_factory() as session:
        await session.execute(delete(Organization).where(Organization.id == org_id))
        await session.commit()
