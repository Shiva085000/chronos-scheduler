"""Worker execution-path tests: per-job timeouts and shutdown semantics.

These run the real `WorkerRunner._execute` coroutine against an in-memory
Job and a stub ExecutionService — no database or Redis required — so they
pin the *routing* of outcomes: timeout → complete_failure with a timeout
reason, success → complete_success, outer cancellation → release_job
(graceful shutdown unchanged by the wait_for wrapper).
"""

import asyncio
import uuid

import pytest

from app.core.config import settings
from app.models import Job, JobStatus
from app.worker.runner import WorkerRunner


class StubExecutor:
    def __init__(self):
        self.failures: list[str] = []
        self.successes: list[dict | None] = []
        self.released: list[uuid.UUID] = []

    async def complete_failure(self, job, worker_id, error):
        self.failures.append(error)
        return True

    async def complete_success(self, job, worker_id, result):
        self.successes.append(result)
        return True

    async def release_job(self, job, worker_id):
        self.released.append(job.id)
        return True


def make_job(task_name: str, payload: dict, timeout_seconds: int) -> Job:
    # Column defaults apply at flush time, not __init__, so set everything
    # the execution path reads explicitly.
    return Job(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        queue="default",
        task_name=task_name,
        payload=payload,
        status=JobStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        timeout_seconds=timeout_seconds,
    )


@pytest.fixture
def runner():
    r = WorkerRunner(settings, name="test-runner")
    r.executor = StubExecutor()  # type: ignore[assignment]
    return r


async def test_timeout_is_recorded_as_normal_failure(runner):
    job = make_job("demo.sleep", {"seconds": 30}, timeout_seconds=1)

    await runner._execute(job)

    assert runner.executor.successes == []
    assert runner.executor.released == []
    assert len(runner.executor.failures) == 1
    error = runner.executor.failures[0]
    assert "timed out after 1s" in error
    assert "attempt 1 of 3" in error


async def test_fast_handler_succeeds_within_timeout(runner):
    job = make_job("demo.echo", {"message": "hi"}, timeout_seconds=300)

    await runner._execute(job)

    assert runner.executor.failures == []
    assert runner.executor.successes == [{"echo": "hi"}]


async def test_handler_exception_still_routes_to_failure(runner):
    job = make_job("demo.always_fail", {"error": "boom"}, timeout_seconds=300)

    await runner._execute(job)

    assert len(runner.executor.failures) == 1
    assert "boom" in runner.executor.failures[0]


async def test_shutdown_cancellation_releases_not_fails(runner):
    """Graceful-shutdown semantics survive the wait_for wrapper: cancelling
    the outer task must release the job (attempt refunded), never record a
    timeout or failure."""
    job = make_job("demo.sleep", {"seconds": 30}, timeout_seconds=300)

    task = asyncio.create_task(runner._execute(job))
    await asyncio.sleep(0.1)  # let the handler start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runner.executor.released == [job.id]
    assert runner.executor.failures == []
    assert runner.executor.successes == []
