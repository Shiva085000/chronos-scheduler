"""Throughput/latency benchmark.

    docker compose exec -T api python -m app.scripts.bench --jobs 2000

Enqueues N `demo.echo` jobs in bulk, waits for the fleet to drain them, and
reports throughput plus claim-wait percentiles measured from the database's
own timestamps (started_at - run_at, finished_at - started_at). Cleans up
its rows afterward so repeated runs don't skew stats or the dashboard.

Method notes:
- demo.echo does no work, so the numbers measure the *scheduler* — claim,
  dispatch, finalize round-trips — not handler time.
- The throughput window is max(finished_at) - min(started_at): pure
  execution-side wall time, excluding enqueue cost.
"""

import argparse
import asyncio
import time
import uuid

from sqlalchemy import delete, func, select, text

from app.db.session import SessionFactory, engine
from app.events import EventBus
from app.core.config import settings
from app.models import Job, JobStatus, User

BENCH_EMAIL = "bench@example.com"


async def ensure_user(session) -> uuid.UUID:
    row = await session.execute(select(User).where(User.email == BENCH_EMAIL))
    user = row.scalar_one_or_none()
    if user is None:
        user = User(email=BENCH_EMAIL, password_hash="not-a-login-user")
        session.add(user)
        await session.commit()
    return user.id


async def run(n_jobs: int) -> None:
    bus = EventBus(settings.redis_url)
    async with SessionFactory() as session:
        owner_id = await ensure_user(session)

        job_ids = [uuid.uuid4() for _ in range(n_jobs)]
        for chunk_start in range(0, n_jobs, 500):
            chunk = job_ids[chunk_start : chunk_start + 500]
            session.add_all(
                Job(
                    id=job_id,
                    owner_id=owner_id,
                    queue="default",
                    task_name="demo.echo",
                    payload={"bench": True},
                    status=JobStatus.PENDING,
                )
                for job_id in chunk
            )
            await session.commit()
        await bus.publish_wake()
        print(f"enqueued {n_jobs} demo.echo jobs; waiting for drain...")

        t_start = time.monotonic()
        try:
            while True:
                done = (
                    await session.execute(
                        select(func.count()).where(
                            Job.id.in_(job_ids), Job.status == JobStatus.SUCCEEDED
                        )
                    )
                ).scalar_one()
                if done >= n_jobs:
                    break
                if time.monotonic() - t_start > 600:
                    raise SystemExit(f"timed out: {done}/{n_jobs} finished")
                await asyncio.sleep(0.5)
        except BaseException:
            # Always clean up, including on timeout/interrupt — leftover
            # bench jobs would skew the next run and pollute the dashboard.
            await session.execute(delete(Job).where(Job.id.in_(job_ids)))
            await session.commit()
            raise

        stats = (
            await session.execute(
                select(
                    func.extract(
                        "epoch", func.max(Job.finished_at) - func.min(Job.started_at)
                    ),
                    func.percentile_cont(0.5).within_group(
                        func.extract("epoch", Job.started_at - Job.run_at)
                    ),
                    func.percentile_cont(0.95).within_group(
                        func.extract("epoch", Job.started_at - Job.run_at)
                    ),
                    func.percentile_cont(0.5).within_group(
                        func.extract("epoch", Job.finished_at - Job.started_at)
                    ),
                    func.percentile_cont(0.95).within_group(
                        func.extract("epoch", Job.finished_at - Job.started_at)
                    ),
                ).where(Job.id.in_(job_ids))
            )
        ).one()
        window, wait_p50, wait_p95, exec_p50, exec_p95 = (
            float(v) for v in stats
        )
        workers = (
            await session.execute(
                text("SELECT count(*) FROM workers WHERE status = 'online'")
            )
        ).scalar_one()

        print(
            f"\nworkers_online={workers}  jobs={n_jobs}\n"
            f"window={window:.2f}s  throughput={n_jobs / window:.1f} jobs/s\n"
            f"claim_wait p50={wait_p50 * 1000:.0f}ms  p95={wait_p95 * 1000:.0f}ms\n"
            f"exec+finalize p50={exec_p50 * 1000:.0f}ms  p95={exec_p95 * 1000:.0f}ms"
        )

        await session.execute(delete(Job).where(Job.id.in_(job_ids)))
        await session.commit()
        print("bench rows cleaned up")

    await bus.close()
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=2000)
    args = parser.parse_args()
    asyncio.run(run(args.jobs))


if __name__ == "__main__":
    main()
