"""Seed demo data: a demo user plus one job of each demo task type.

    docker compose exec api python -m app.scripts.seed

Login afterwards with demo@example.com / demo12345.
"""

import asyncio

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory, engine
from app.events import EventBus
from app.schemas.job import JobCreate
from app.services.auth_service import AuthService
from app.services.exceptions import EmailAlreadyRegisteredError
from app.services.job_service import JobService

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo12345"

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

        jobs = JobService(session, bus)
        for data in DEMO_JOBS:
            job, created = await jobs.enqueue(user.id, data)
            print(
                f"{'enqueued' if created else 'exists  '} {job.task_name:22s}"
                f" id={job.id}"
            )
    await bus.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
