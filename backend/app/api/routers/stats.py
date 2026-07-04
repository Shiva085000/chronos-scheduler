from fastapi import APIRouter

from app.api.deps import CurrentUser, Stats
from app.schemas.stats import StatsOverview

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get(
    "/overview",
    response_model=StatsOverview,
    summary="Cluster-wide scheduler metrics",
)
async def overview(_: CurrentUser, stats: Stats) -> StatsOverview:
    return await stats.overview()
