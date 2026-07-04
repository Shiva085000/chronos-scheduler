"""Prometheus scrape endpoint.

Unauthenticated by convention (like /healthz): metrics endpoints are scraped
by infrastructure, not users, and expose aggregate counts only — no job
payloads, ids, or tenant data. In a hardened deployment, bind it to an
internal listener or gate it at the reverse proxy.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.api.deps import DbSession
from app.services.metrics_service import MetricsService

router = APIRouter(tags=["health"])

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", summary="Prometheus metrics", response_class=PlainTextResponse)
async def metrics(session: DbSession) -> PlainTextResponse:
    body = await MetricsService(session).render()
    return PlainTextResponse(content=body, media_type=PROMETHEUS_CONTENT_TYPE)
