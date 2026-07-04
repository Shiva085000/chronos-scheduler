"""FastAPI application factory."""

import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import auth, dlq, health, jobs, metrics, stats, workers
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import engine
from app.events import EventBus

logger = structlog.get_logger(__name__)

TAGS_METADATA = [
    {"name": "auth", "description": "Registration and JWT login."},
    {
        "name": "jobs",
        "description": (
            "Enqueue and manage jobs. Jobs are executed at-least-once by "
            "the worker fleet with per-job retry policies."
        ),
    },
    {
        "name": "dead letter queue",
        "description": "Jobs that exhausted their retry budget.",
    },
    {"name": "workers", "description": "Worker fleet visibility."},
    {"name": "stats", "description": "Cluster metrics for the dashboard."},
    {"name": "health", "description": "Liveness/readiness probes."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(debug=settings.debug, service="api")
    app.state.event_bus = EventBus(settings.redis_url)
    logger.info("api.started", environment=settings.environment)
    yield
    await app.state.event_bus.close()
    await engine.dispose()
    logger.info("api.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=(
            "A production-inspired distributed job scheduler: atomic job "
            "claiming via `FOR UPDATE SKIP LOCKED`, lease-based worker "
            "liveness with heartbeats, exponential-backoff retries, a dead "
            "letter queue, and idempotent enqueues."
        ),
        version="1.0.0",
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["x-request-id"] = request_id
        if not request.url.path.startswith(("/healthz", "/readyz", "/metrics")):
            logger.info(
                "http.request",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        return response

    app.include_router(health.router)
    app.include_router(metrics.router)
    api = "/api/v1"
    app.include_router(auth.router, prefix=api)
    app.include_router(jobs.router, prefix=api)
    app.include_router(dlq.router, prefix=api)
    app.include_router(workers.router, prefix=api)
    app.include_router(stats.router, prefix=api)
    return app


app = create_app()
