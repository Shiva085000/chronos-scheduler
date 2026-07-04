"""Redis pub/sub wake-up channel.

Redis is a latency optimization, not a source of truth: enqueues publish
a wake signal so idle workers claim immediately instead of waiting out
their poll interval. Every operation here is best-effort — if Redis is
down, publishers log and move on, and workers fall back to plain
polling. Losing Redis costs latency, never correctness.
"""

import asyncio

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger(__name__)

WAKE_CHANNEL = "chronos:wake"


class EventBus:
    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._pubsub: aioredis.client.PubSub | None = None

    async def publish_wake(self) -> None:
        try:
            await self._redis.publish(WAKE_CHANNEL, "wake")
        except Exception as exc:  # noqa: BLE001 — degrade, never fail the caller
            logger.warning("event_bus.publish_failed", error=str(exc))

    async def wait_for_wake(self, timeout: float) -> bool:
        """Block up to `timeout` seconds for a wake signal.

        Returns True if woken by an event, False on timeout. Raises
        nothing: any Redis failure is converted into a plain sleep so the
        worker's poll loop keeps functioning without Redis.
        """
        try:
            if self._pubsub is None:
                self._pubsub = self._redis.pubsub()
                await self._pubsub.subscribe(WAKE_CHANNEL)
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=timeout
            )
            return message is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("event_bus.wait_failed_falling_back_to_poll", error=str(exc))
            self._pubsub = None
            await asyncio.sleep(timeout)
            return False

    async def get_cached(self, key: str) -> str | None:
        try:
            return await self._redis.get(key)
        except Exception:  # noqa: BLE001
            return None

    async def set_cached(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            await self._redis.set(key, value, ex=ttl_seconds)
        except Exception:  # noqa: BLE001
            pass

    async def check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> bool:
        """Fixed-window counter. Returns True if the call is allowed.

        Fails OPEN: rate limiting is protection, not correctness — Redis
        being down must not lock every user out of login.
        """
        try:
            full_key = f"chronos:ratelimit:{key}"
            count = await self._redis.incr(full_key)
            if count == 1:
                await self._redis.expire(full_key, window_seconds)
            return count <= limit
        except Exception as exc:  # noqa: BLE001
            logger.warning("event_bus.rate_limit_unavailable", error=str(exc))
            return True

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        try:
            if self._pubsub is not None:
                await self._pubsub.close()
            await self._redis.aclose()
        except Exception:  # noqa: BLE001
            pass
