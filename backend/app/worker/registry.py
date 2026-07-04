"""Task handler registry.

Handlers are plain async functions registered by name. The scheduler
stores only the task *name* and a JSON payload — code and data stay
separate, so deploying new handler code never requires migrating queued
jobs.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Execution context passed to every handler.

    `attempt` lets handlers implement attempt-aware behavior; `job_id` is
    the natural idempotency scope for any external side effects the
    handler performs (execution is at-least-once).
    """

    job_id: UUID
    attempt: int
    max_attempts: int


TaskHandler = Callable[[TaskContext, dict[str, Any]], Awaitable[dict[str, Any] | None]]

_registry: dict[str, TaskHandler] = {}


class UnknownTaskError(Exception):
    pass


def task(name: str) -> Callable[[TaskHandler], TaskHandler]:
    def decorator(fn: TaskHandler) -> TaskHandler:
        if name in _registry:
            raise ValueError(f"duplicate task handler: {name}")
        _registry[name] = fn
        return fn

    return decorator


def get_handler(name: str) -> TaskHandler:
    try:
        return _registry[name]
    except KeyError:
        raise UnknownTaskError(f"no handler registered for task '{name}'") from None


def registered_tasks() -> list[str]:
    return sorted(_registry)
