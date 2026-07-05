"""Seed demo data: a demo user plus one job of each demo task type, a
concurrency-capped queue, a recurring schedule, a batch, plus bonus
feature demos: a viewer user (RBAC), a workflow DAG, and a sharded queue.

    docker compose exec api python -m app.scripts.seed

Login afterwards with demo@example.com / demo12345.
"""

import asyncio

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory, engine
from app.events import EventBus
from app.models.user import UserRole
from app.repositories.users import UserRepository
from app.schemas.job import JobCreate
from app.schemas.queue import QueueCreate
from app.schemas.schedule import ScheduleCreate
from app.services.auth_service import AuthService
from app.services.exceptions import ConflictError, EmailAlreadyRegisteredError
from app.services.job_service import JobService
from app.services.queue_service import QueueService
from app.services.schedule_service import ScheduleService

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo12345"

VIEWER_EMAIL = "viewer@example.com"
VIEWER_PASSWORD = "viewer12345"

DEMO_JOBS = [
    JobCreate(task_name="demo.echo", payload={"message": "hello from the seed"}),
    JobCreate(task_name="demo.sleep", payload={"seconds": 10}),
    JobCreate(
        task_name="demo.fail_until",
        payload={"succeed_on_attempt": 3},
        max_attempts=5,
        backoff_base_seconds=5,
    ),
    JobCreate(task_name="demo.flaky", payload={"failure_rate": 0.5}, max_attempts=4),
    JobCreate(
        task_name="demo.always_fail",
        payload={"error": "seeded DLQ example"},
        max_attempts=2,
        backoff_base_seconds=3,
    ),
    JobCreate(
        task_name="demo.echo",
        payload={"message": "idempotency demo"},
        idempotency_key="seed-idempotent-job",
    ),
]

# Three sleeps into a queue capped at max_concurrency=1: watch them run
# strictly one at a time on the dashboard no matter how many workers idle.
DEMO_BATCH = [
    JobCreate(
        task_name="demo.sleep",
        payload={"seconds": 8, "n": i},
        queue="capped",
        idempotency_key=f"seed-capped-batch-{i}",
    )
    for i in range(3)
]

DEMO_SCHEDULE = ScheduleCreate(
    task_name="demo.echo",
    payload={"message": "cron heartbeat"},
    cron_expr="*/2 * * * *",
    queue="default",
)


async def main() -> None:
    configure_logging(debug=True, service="seed")
    bus = EventBus(settings.redis_url)
    async with SessionFactory() as session:
        auth = AuthService(session)
        try:
            user = await auth.register(DEMO_EMAIL, DEMO_PASSWORD)
            print(f"created user {DEMO_EMAIL} / {DEMO_PASSWORD}")
        except EmailAlreadyRegisteredError:
            user = await auth.users.get_by_email(DEMO_EMAIL)
            print(f"user {DEMO_EMAIL} already exists")

        # --- RBAC: create a viewer user in the same org ---
        try:
            viewer = await auth.register(VIEWER_EMAIL, VIEWER_PASSWORD)
            viewer.org_id = user.org_id
            viewer.role = UserRole.VIEWER
            await session.commit()
            print(f"created viewer user {VIEWER_EMAIL} / {VIEWER_PASSWORD}")
        except EmailAlreadyRegisteredError:
            await session.rollback()
            print(f"viewer user {VIEWER_EMAIL} already exists")
    
    # Start a fresh session for the rest of the script to avoid rollback artifacts
    async with SessionFactory() as session:
        auth = AuthService(session)
        # Re-fetch user to ensure it's attached to the active transaction and fully loaded
        user = await auth.users.get_by_email(DEMO_EMAIL)
        # Detach it with its attributes loaded: the "already seeded" paths
        # below roll the session back, which would expire an *attached*
        # object and make the next user.org_id read blow up (MissingGreenlet
        # — no sync refresh in an async session). The services only read
        # user.id / org_id / role, so a detached snapshot is exactly right.
        session.expunge(user)

        queues = QueueService(session)
        try:
            await queues.create(user, QueueCreate(name="capped", max_concurrency=1))
            print("created queue 'capped' (max_concurrency=1)")
        except ConflictError:
            print("queue 'capped' already exists")

        # --- Queue sharding demo ---
        try:
            await queues.create(
                user, QueueCreate(name="sharded", shard_key=1)
            )
            print("created queue 'sharded' (shard_key=1)")
        except ConflictError:
            print("queue 'sharded' already exists")

        jobs = JobService(session, bus)
        for data in DEMO_JOBS:
            job, created = await jobs.enqueue(user, data)
            print(
                f"{'enqueued' if created else 'exists  '} {job.task_name:22s}"
                f" id={job.id}"
            )

        try:
            batch_id, batch = await jobs.enqueue_batch(user, DEMO_BATCH)
            print(f"enqueued batch of {len(batch)} demo.sleep jobs id={batch_id}")
        except ConflictError:
            print("capped-queue batch already seeded")

        # --- Workflow dependency demo: A → B → C ---
        try:
            job_a, created_a = await jobs.enqueue(
                user,
                JobCreate(
                    task_name="demo.echo",
                    payload={"step": "A", "message": "workflow step A"},
                    queue="default",
                    idempotency_key="seed-workflow-a",
                ),
            )
            if created_a:
                job_b, _ = await jobs.enqueue(
                    user,
                    JobCreate(
                        task_name="demo.echo",
                        payload={"step": "B", "message": "workflow step B"},
                        queue="default",
                        idempotency_key="seed-workflow-b",
                        depends_on=[job_a.id],
                    ),
                )
                job_c, _ = await jobs.enqueue(
                    user,
                    JobCreate(
                        task_name="demo.echo",
                        payload={"step": "C", "message": "workflow step C"},
                        queue="default",
                        idempotency_key="seed-workflow-c",
                        depends_on=[job_b.id],
                    ),
                )
                print(
                    f"created workflow A({job_a.id}) → B({job_b.id}) → C({job_c.id})"
                )
            else:
                print("workflow demo already seeded")
        except ConflictError:
            print("workflow demo already seeded")

        schedules = ScheduleService(session)
        existing, _ = await schedules.list(user)
        if any(s.cron_expr == DEMO_SCHEDULE.cron_expr for s in existing):
            print("demo schedule already exists")
        else:
            schedule = await schedules.create(user, DEMO_SCHEDULE)
            print(
                f"created schedule {schedule.cron_expr!r} -> demo.echo "
                f"(next run {schedule.next_run_at:%H:%M:%S} UTC)"
            )
    await bus.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
