from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import Bus, DbSession

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe (checks Postgres and Redis)")
async def readyz(session: DbSession, bus: Bus, response: Response) -> dict:
    checks = {"postgres": False, "redis": False}
    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception:  # noqa: BLE001
        pass
    checks["redis"] = await bus.ping()

    # Redis degrades gracefully (polling fallback), so only Postgres gates
    # readiness.
    if not checks["postgres"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if checks["postgres"] else "degraded", **checks}
