"""Integration tests: queue pause and concurrency-cap enforcement in the
claim path.

Requires a running Postgres (set TEST_DATABASE_URL); skipped otherwise.
Run inside docker compose with:

    docker compose exec api sh -c \
      "TEST_DATABASE_URL=$DATABASE_URL python -m pytest tests/test_queue_controls.py -v"

Invariants under test:
- a paused queue hands out nothing, and resuming reopens it;
- a queue with max_concurrency=N never has more than N RUNNING jobs,
  even with many claimers racing, because admission happens under the
  queue's row lock.
"""

import asyncio
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


async def _cleanup(session_factory, org_id, worker_ids):
    from sqlalchemy import delete

    from app.models import Organization, Worker

    async with session_factory() as session:
        await session.execute(delete(Worker).where(Worker.id.in_(worker_ids)))
        await session.execute(delete(Organization).where(Organization.id == org_id))
        await session.commit()


async def _make_worker(session_factory, name):
    from app.models import Worker, WorkerStatus

    async with session_factory() as session:
        worker = Worker(name=name, status=WorkerStatus.ONLINE, concurrency=8)
        session.add(worker)
        await session.flush()
        worker_id = worker.id
        await session.commit()
    return worker_id


async def test_paused_queue_is_not_claimed_until_resumed(session_factory):
    from sqlalchemy import update

    from app.models import Job, Queue
    from app.repositories.jobs import JobRepository
    from tests.factories import create_owner_with_queue

    queue_name = f"pausetest-{uuid.uuid4()}"
    async with session_factory() as session:
        org, user, queue = await create_owner_with_queue(
            session, queue_name, paused=True
        )
        session.add(
            Job(
                owner_id=user.id,
                queue_id=queue.id,
                queue=queue_name,
                task_name="demo.echo",
            )
        )
        await session.commit()
        org_id, queue_id = org.id, queue.id

    worker_id = await _make_worker(session_factory, "pause-claimer")
    try:
        async with session_factory() as session:
            claimed = await JobRepository(session).claim_batch(
                worker_id, queues=[queue_name], limit=10, lease_seconds=60
            )
            await session.commit()
        assert claimed == [], "a paused queue handed out a job"

        async with session_factory() as session:
            await session.execute(
                update(Queue).where(Queue.id == queue_id).values(paused=False)
            )
            await session.commit()

        async with session_factory() as session:
            claimed = await JobRepository(session).claim_batch(
                worker_id, queues=[queue_name], limit=10, lease_seconds=60
            )
            await session.commit()
        assert len(claimed) == 1, "resume did not reopen the queue"
    finally:
        await _cleanup(session_factory, org_id, [worker_id])


async def test_concurrency_cap_is_never_exceeded(session_factory):
    from app.models import Job
    from app.repositories.jobs import JobRepository
    from tests.factories import create_owner_with_queue

    cap = 3
    n_jobs = 12
    n_claimers = 8
    queue_name = f"captest-{uuid.uuid4()}"

    async with session_factory() as session:
        org, user, queue = await create_owner_with_queue(
            session, queue_name, max_concurrency=cap
        )
        session.add_all(
            Job(
                owner_id=user.id,
                queue_id=queue.id,
                queue=queue_name,
                task_name="demo.echo",
            )
            for _ in range(n_jobs)
        )
        await session.commit()
        org_id = org.id

    worker_ids = [
        await _make_worker(session_factory, f"cap-claimer-{i}")
        for i in range(n_claimers)
    ]

    async def claim_once(worker_id):
        async with session_factory() as session:
            batch = await JobRepository(session).claim_batch(
                worker_id, queues=[queue_name], limit=10, lease_seconds=60
            )
            await session.commit()
            return batch

    try:
        # Round 1: everyone races; exactly `cap` jobs may start.
        results = await asyncio.gather(*(claim_once(w) for w in worker_ids))
        first_round = [job for batch in results for job in batch]
        assert len(first_round) == cap, (
            f"expected exactly {cap} admissions, got {len(first_round)}"
        )

        # While the cap is saturated, nobody gets anything.
        results = await asyncio.gather(*(claim_once(w) for w in worker_ids))
        assert all(not batch for batch in results), "cap was exceeded"

        # Finishing one job frees exactly one slot.
        done = first_round[0]
        async with session_factory() as session:
            await JobRepository(session).finish_success(
                done.id, done.locked_by, None
            )
            await session.commit()

        results = await asyncio.gather(*(claim_once(w) for w in worker_ids))
        second_round = [job for batch in results for job in batch]
        assert len(second_round) == 1, (
            f"one freed slot must admit exactly one job, got {len(second_round)}"
        )
    finally:
        await _cleanup(session_factory, org_id, worker_ids)
