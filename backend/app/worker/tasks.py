"""Demo task handlers.

Each exists to demonstrate a specific reliability behavior end-to-end:
success, latency, deterministic retries, probabilistic flakiness, and
guaranteed DLQ delivery.
"""

import asyncio
import random
from typing import Any

from app.worker.registry import TaskContext, task


@task("demo.echo")
async def echo(ctx: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Succeeds immediately; returns its input."""
    return {"echo": payload.get("message", "hello, chronos")}


@task("demo.sleep")
async def sleep(ctx: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Sleeps `seconds` (capped at 120) — useful for watching leases,
    heartbeats, and graceful shutdown while a job is in flight."""
    seconds = min(float(payload.get("seconds", 5)), 120.0)
    await asyncio.sleep(seconds)
    return {"slept_seconds": seconds}


@task("demo.fail_until")
async def fail_until(ctx: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Fails deterministically until `succeed_on_attempt` (default 3) —
    demonstrates the retry/backoff pipeline ending in success."""
    succeed_on = int(payload.get("succeed_on_attempt", 3))
    if ctx.attempt < succeed_on:
        raise RuntimeError(
            f"deliberate failure on attempt {ctx.attempt} "
            f"(will succeed on attempt {succeed_on})"
        )
    return {"succeeded_on_attempt": ctx.attempt}


@task("demo.flaky")
async def flaky(ctx: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Fails with probability `failure_rate` (default 0.5)."""
    rate = float(payload.get("failure_rate", 0.5))
    if random.random() < rate:
        raise RuntimeError(f"flaky failure (failure_rate={rate})")
    return {"attempt": ctx.attempt}


@task("demo.always_fail")
async def always_fail(ctx: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Never succeeds — exhausts its retry budget and lands in the DLQ."""
    raise RuntimeError(payload.get("error", "this task always fails"))
