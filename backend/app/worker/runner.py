"""The worker process: claim → execute → finalize, with three concurrent
maintenance loops (consume, heartbeat, reaper) and graceful shutdown.

Concurrency model: a single asyncio event loop per worker process runs up
to `worker_concurrency` handler coroutines. Handlers are expected to be
I/O-bound (the common case for job systems); CPU-bound work would call
for a process pool, which is out of scope here.
"""

import asyncio
import os
import traceback
import uuid
from pathlib import Path

import structlog

from app.core.config import Settings
from app.db.session import SessionFactory, engine
from app.events import EventBus
from app.models import Job, WorkerStatus
from app.services.execution_service import ExecutionService
from app.worker import tasks as _tasks  # noqa: F401 — registers demo handlers
from app.worker.registry import TaskContext, get_handler, registered_tasks

logger = structlog.get_logger(__name__)

# Touched after every successful heartbeat; the container healthcheck
# treats a stale mtime (> 2 × heartbeat interval) as unhealthy, so a
# wedged-but-running worker is visible to the orchestrator.
HEALTH_FILE = Path(os.environ.get("WORKER_HEALTH_FILE", "/tmp/chronos-worker-heartbeat"))


def _touch_health_file() -> None:
    try:
        HEALTH_FILE.touch()
    except OSError:  # e.g. read-only fs or missing /tmp on a dev host
        pass


class WorkerRunner:
    def __init__(self, settings: Settings, name: str) -> None:
        self.settings = settings
        self.id = uuid.uuid4()
        self.name = name
        self.executor = ExecutionService(SessionFactory)
        self.bus = EventBus(settings.redis_url)
        self._draining = asyncio.Event()
        self._inflight: dict[asyncio.Task, Job] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        structlog.contextvars.bind_contextvars(worker_id=str(self.id))
        await self.executor.register_worker(
            self.id, self.name, self.settings.worker_concurrency
        )
        logger.info(
            "worker.started",
            name=self.name,
            concurrency=self.settings.worker_concurrency,
            tasks=registered_tasks(),
        )

        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
        reaper = asyncio.create_task(self._reaper_loop(), name="reaper")
        try:
            await self._consume_loop()
        finally:
            await self._shutdown(heartbeat, reaper)

    def request_shutdown(self) -> None:
        """Signal-handler entrypoint; safe to call multiple times."""
        if not self._draining.is_set():
            logger.info("worker.shutdown_requested")
            self._draining.set()

    async def _shutdown(
        self, heartbeat: asyncio.Task, reaper: asyncio.Task
    ) -> None:
        # 1. Stop claiming; announce DRAINING so the fleet view shows it.
        await self.executor.set_worker_status(self.id, WorkerStatus.DRAINING)
        logger.info("worker.draining", inflight=len(self._inflight))

        # 2. Give in-flight jobs a grace window to finish normally. The
        #    heartbeat keeps running so their leases stay alive meanwhile.
        if self._inflight:
            await asyncio.wait(
                set(self._inflight),
                timeout=self.settings.shutdown_grace_seconds,
            )

        # 3. Whatever is still running gets cancelled and *released*:
        #    back to PENDING immediately, attempt refunded — much better
        #    than making the job wait out a lease expiry.
        leftovers = [t for t in self._inflight if not t.done()]
        for task in leftovers:
            task.cancel()
        if leftovers:
            await asyncio.gather(*leftovers, return_exceptions=True)

        heartbeat.cancel()
        reaper.cancel()
        await asyncio.gather(heartbeat, reaper, return_exceptions=True)

        await self.executor.set_worker_status(
            self.id, WorkerStatus.OFFLINE, stopped=True
        )
        await self.bus.close()
        await engine.dispose()
        logger.info("worker.stopped")

    # ------------------------------------------------------------------
    # main loops
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        while not self._draining.is_set():
            free_slots = self.settings.worker_concurrency - len(self._inflight)
            claimed: list[Job] = []
            if free_slots > 0:
                try:
                    claimed = await self.executor.claim_jobs(
                        self.id,
                        queues=self.settings.worker_queue_list,
                        limit=free_slots,
                        lease_seconds=self.settings.lease_seconds,
                    )
                except Exception:  # noqa: BLE001 — DB blip: back off, retry
                    logger.exception("worker.claim_failed")
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

            for job in claimed:
                task = asyncio.create_task(
                    self._execute(job), name=f"job-{job.id}"
                )
                self._inflight[task] = job
                task.add_done_callback(self._inflight.pop)

            if claimed:
                continue  # queue may have more ready work; claim again now

            # Idle or saturated: block on the Redis wake channel with the
            # poll interval as timeout. Either an enqueue wakes us early or
            # we poll — Redis being down only costs latency.
            await self.bus.wait_for_wake(self.settings.poll_interval_seconds)

    async def _execute(self, job: Job) -> None:
        log = logger.bind(
            job_id=str(job.id), task_name=job.task_name, attempt=job.attempt_count
        )
        log.info("job.started")
        try:
            handler = get_handler(job.task_name)
            ctx = TaskContext(
                job_id=job.id,
                attempt=job.attempt_count,
                max_attempts=job.max_attempts,
            )
            # wait_for cancels the handler at the deadline and raises
            # TimeoutError here. If the *outer* task is cancelled instead
            # (graceful shutdown), wait_for re-raises CancelledError, so the
            # shutdown release path below is unaffected.
            result = await asyncio.wait_for(
                handler(ctx, job.payload), timeout=job.timeout_seconds
            )
        except asyncio.CancelledError:
            # Graceful shutdown cancelled us mid-run: release, don't fail.
            await asyncio.shield(self.executor.release_job(job, self.id))
            log.info("job.released_on_shutdown")
            raise
        except TimeoutError:
            # A timeout is a normal failure: it burns the attempt and goes
            # through the same retry/DLQ decision as a handler exception.
            error = (
                f"execution timed out after {job.timeout_seconds}s "
                f"(attempt {job.attempt_count} of {job.max_attempts})"
            )
            await self.executor.complete_failure(job, self.id, error)
            log.warning("job.timed_out", timeout_seconds=job.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — any handler error is a job failure
            error = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-4000:]
            await self.executor.complete_failure(job, self.id, error)
            log.warning("job.failed", error=str(exc))
        else:
            if await self.executor.complete_success(job, self.id, result):
                log.info("job.succeeded")

    async def _heartbeat_loop(self) -> None:
        _touch_health_file()  # healthy from boot; first beat is 10s away
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            try:
                extended = await self.executor.heartbeat(
                    self.id, self.settings.lease_seconds
                )
                _touch_health_file()
                logger.debug("worker.heartbeat", leases_extended=extended)
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.exception("worker.heartbeat_failed")

    async def _reaper_loop(self) -> None:
        """Every worker hosts a reaper loop; a Postgres advisory lock
        inside reap_once ensures only one sweep runs cluster-wide."""
        while True:
            await asyncio.sleep(self.settings.reaper_interval_seconds)
            try:
                reclaimed = await self.executor.reap_once(
                    self.settings.worker_offline_after_seconds
                )
                if reclaimed:
                    # Reclaimed jobs may be immediately runnable.
                    await self.bus.publish_wake()
            except Exception:  # noqa: BLE001
                logger.exception("worker.reaper_failed")
