"""Application configuration.

All tunables are environment-driven so the same image can run as the API
server or as a worker with different knobs. Defaults are sane for local
docker-compose development.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Chronos Job Scheduler"
    environment: str = "development"
    debug: bool = False

    # --- infrastructure -------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://chronos:chronos@localhost:5432/chronos"
    )
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- auth -----------------------------------------------------------
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24
    login_rate_limit_per_minute: int = 10

    # --- worker / lease protocol ----------------------------------------
    # A lease must be several heartbeats long so a single missed heartbeat
    # (GC pause, transient network blip) does not cause spurious reclaims.
    worker_concurrency: int = 4
    worker_queues: str = "default"  # comma-separated list this worker consumes
    lease_seconds: int = 30
    heartbeat_seconds: int = 10
    poll_interval_seconds: float = 2.0
    reaper_interval_seconds: float = 5.0
    scheduler_interval_seconds: float = 5.0
    worker_offline_after_seconds: int = 60
    shutdown_grace_seconds: float = 20.0

    # --- fairness ---------------------------------------------------------
    # A waiting job gains +1 effective priority per interval, capped at the
    # boost. With interval 60s and boost 200, a priority −100 job outranks a
    # sustained stream of priority +100 jobs after at most 200 minutes —
    # starvation is bounded instead of unbounded. Set interval to 0 to
    # disable aging.
    priority_aging_interval_seconds: int = 60
    priority_aging_max_boost: int = 200

    # --- retry policy defaults (overridable per job) ----------------------
    default_max_attempts: int = 3
    default_backoff_base_seconds: int = 5
    default_backoff_factor: float = 2.0
    default_backoff_max_seconds: int = 300

    # --- stats ----------------------------------------------------------
    stats_cache_ttl_seconds: int = 3

    # --- AI summaries ---------------------------------------------------
    gemini_api_key: str = ""

    # --- sharding -------------------------------------------------------
    # Pin a worker to a specific queue shard. None = consume all shards.
    worker_shard: int | None = None

    @property
    def worker_queue_list(self) -> list[str]:
        return [q.strip() for q in self.worker_queues.split(",") if q.strip()]

    @model_validator(mode="after")
    def _normalize_database_scheme(self) -> "Settings":
        """Managed Postgres providers hand out postgres:// / postgresql://
        URLs; SQLAlchemy needs the asyncpg dialect spelled out."""
        for prefix in ("postgres://", "postgresql://"):
            if self.database_url.startswith(prefix):
                self.database_url = (
                    "postgresql+asyncpg://" + self.database_url[len(prefix):]
                )
        return self

    @model_validator(mode="after")
    def _refuse_default_secret_outside_development(self) -> "Settings":
        """Fail fast at boot: a deployment that forgot JWT_SECRET must not
        come up with a publicly known signing key (total auth bypass)."""
        if self.environment != "development" and self.jwt_secret == DEFAULT_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET is still the development default but "
                f"ENVIRONMENT={self.environment!r}; refusing to start. "
                "Set a real JWT_SECRET."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
