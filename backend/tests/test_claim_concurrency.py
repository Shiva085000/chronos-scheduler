"""Integration test: atomic claiming under concurrency.

Requires a running Postgres (set TEST_DATABASE_URL); skipped otherwise.
Run inside docker compose with:

    docker compose exec api sh -c \
      "TEST_DATABASE_URL=$DATABASE_URL python -m pytest tests/test_claim_concurrency.py -v"

The core invariant: N concurrent claimers over M jobs never hand the
same job to two workers, and every ready job is claimed exactly once.
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


async def test_concurrent_claimers_never_double_claim(session_factory):
    from app.models import Job, User
    from app.models import Worker, WorkerStatus
    from app.repositories.jobs import JobRepository

    n_jobs = 50
    n_claimers = 8
    # A unique queue isolates this test from any live worker fleet sharing
    # the database — fleet workers only claim from their configured queues.
    test_queue = f"claimtest-{uuid.uuid4()}"

    async with session_factory() as session:
        from tests.factories import create_owner_with_queue

        org, user, queue = await create_owner_with_queue(session, test_queue)
        jobs = [
            Job(
                owner_id=user.id,
                task_name="demo.echo",
                queue=test_queue,
                queue_id=queue.id,
            )
            for _ in range(n_jobs)
        ]
        session.add_all(jobs)
        workers = [
            Worker(name=f"claimer-{i}", status=WorkerStatus.ONLINE, concurrency=1)
            for i in range(n_claimers)
        ]
        session.add_all(workers)
        await session.flush()  # assign python-side UUID defaults
        job_ids = [j.id for j in jobs]
        worker_ids = [w.id for w in workers]
        await session.commit()

    async def claim_all(worker_id):
        claimed = []
        while True:
            async with session_factory() as session:
                repo = JobRepository(session)
                batch = await repo.claim_batch(
                    worker_id, queues=[test_queue], limit=5, lease_seconds=60
                )
                await session.commit()
            if not batch:
                return claimed
            claimed.extend(j.id for j in batch)

    try:
        results = await asyncio.gather(*(claim_all(wid) for wid in worker_ids))

        all_claimed = [
            job_id for worker_claims in results for job_id in worker_claims
        ]
        assert len(all_claimed) == len(set(all_claimed)), "a job was double-claimed"
        assert set(all_claimed) == set(job_ids), "some jobs were never claimed"
    finally:
        # Leave no residue when running against a live database. Deleting
        # the org cascades users -> jobs and projects -> queues.
        from sqlalchemy import delete

        from app.models import Organization

        async with session_factory() as session:
            await session.execute(delete(Worker).where(Worker.id.in_(worker_ids)))
            await session.execute(
                delete(Organization).where(Organization.id == org.id)
            )
            await session.commit()
