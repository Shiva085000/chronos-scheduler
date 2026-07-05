"""AI-powered failure summaries using Google Gemini.

When a job lands in the DLQ, this service sends its error text to Gemini
and gets back a 2-3 sentence operator-facing explanation. The result is
cached in Redis (keyed by error hash) so identical failures don't re-call
the API.

Graceful degradation: if GEMINI_API_KEY is empty or the call fails, the
service returns None and logs a warning — no feature depends on it.
"""

import hashlib

import structlog

from app.core.config import settings
from app.events import EventBus

logger = structlog.get_logger(__name__)

AI_CACHE_PREFIX = "chronos:ai:summary:"
AI_CACHE_TTL = 60 * 60 * 24  # 24h — error text doesn't change

SYSTEM_PROMPT = (
    "You are an SRE assistant. Given a job failure error, produce a short "
    "summary (2-3 sentences max) that: 1) explains the likely root cause "
    "in plain English, 2) suggests one actionable fix. Be concise."
)


class AISummaryService:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    async def generate_summary(
        self,
        error_text: str,
        task_name: str,
        attempt_count: int,
    ) -> str | None:
        """Generate a failure summary. Returns None on any failure."""
        if not settings.gemini_api_key:
            return None

        # Check Redis cache first
        cache_key = AI_CACHE_PREFIX + hashlib.sha256(
            error_text.encode()
        ).hexdigest()[:16]
        cached = await self.bus.get_cached(cache_key)
        if cached:
            return cached

        try:
            summary = await self._call_gemini(error_text, task_name, attempt_count)
            if summary:
                await self.bus.set_cached(cache_key, summary, AI_CACHE_TTL)
            return summary
        except Exception:
            logger.warning(
                "ai_summary.generation_failed",
                task_name=task_name,
                exc_info=True,
            )
            return None

    async def _call_gemini(
        self,
        error_text: str,
        task_name: str,
        attempt_count: int,
    ) -> str | None:
        """Call Gemini REST API directly (no SDK dependency)."""
        import httpx

        prompt = (
            f"Task: {task_name}\n"
            f"Failed after {attempt_count} attempts.\n"
            f"Last error:\n{error_text[:2000]}"
        )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={settings.gemini_api_key}"
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "maxOutputTokens": 200,
                "temperature": 0.3,
            },
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        return parts[0]["text"].strip() if parts else None
