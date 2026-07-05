"""AI-powered failure summaries — Gemini behind a LangGraph pipeline.

When a job lands in the DLQ, a multi-agent LangGraph pipeline
(triage → diagnose → remediate → compose; see failure_pipeline.py)
produces the operator-facing note stored on the job. Results are cached
in Redis (keyed by error hash) so identical failures don't re-run the
pipeline.

Graceful degradation: if GEMINI_API_KEY is empty or anything in the
pipeline fails, the service returns None and logs a warning — no feature
depends on it.
"""

import hashlib

import structlog

from app.core.config import settings
from app.events import EventBus
from app.services.failure_pipeline import analyze_failure

logger = structlog.get_logger(__name__)

AI_CACHE_PREFIX = "chronos:ai:summary:"
AI_CACHE_TTL = 60 * 60 * 24  # 24h — error text doesn't change


def _make_llm():
    # Imported lazily so the worker boots instantly when the feature is off.
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=settings.gemini_api_key,
        temperature=0.3,
        max_output_tokens=300,
        timeout=15,
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
        """Run the analysis pipeline. Returns None on any failure."""
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
            summary = await analyze_failure(
                _make_llm(),
                task_name=task_name,
                error_text=error_text,
                attempt_count=attempt_count,
            )
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
