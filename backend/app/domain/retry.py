"""Retry policy decisions — pure functions, no I/O.

Keeping this logic free of SQLAlchemy/FastAPI makes the retry semantics
unit-testable and forces a single source of truth: the worker's failure
path and the reaper's lease-expiry path both call `decide_failure`.
"""

import enum
import random
from dataclasses import dataclass


class RetryStrategy(str, enum.Enum):
    """How the delay between attempts grows.

    FIXED       -> base
    LINEAR      -> base * attempt_number
    EXPONENTIAL -> base * factor^(attempt_number - 1)

    All three are capped at backoff_max_seconds and jittered; `factor`
    only participates in EXPONENTIAL.
    """

    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    backoff_base_seconds: int
    backoff_factor: float
    backoff_max_seconds: int
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL


@dataclass(frozen=True, slots=True)
class FailureDecision:
    retry: bool
    delay_seconds: float  # 0 when retry is False


def compute_backoff_seconds(
    policy: RetryPolicy, attempt_number: int, rng: random.Random | None = None
) -> float:
    """Backoff per the policy's strategy, with an upper cap and up-to-20%
    jitter.

    Jitter prevents a thundering herd when many jobs fail at once (e.g. a
    downstream dependency outage) and would otherwise all retry in the
    same instant.
    """
    if attempt_number < 1:
        raise ValueError("attempt_number must be >= 1")
    if policy.strategy is RetryStrategy.FIXED:
        raw = float(policy.backoff_base_seconds)
    elif policy.strategy is RetryStrategy.LINEAR:
        raw = float(policy.backoff_base_seconds * attempt_number)
    else:
        raw = policy.backoff_base_seconds * (
            policy.backoff_factor ** (attempt_number - 1)
        )
    capped = min(raw, float(policy.backoff_max_seconds))
    jitter = capped * 0.2 * (rng or random).random()
    return capped + jitter


def decide_failure(
    policy: RetryPolicy, attempt_number: int, rng: random.Random | None = None
) -> FailureDecision:
    """Decide what happens after attempt `attempt_number` failed.

    Lease expirations count as failed attempts on purpose: a job that
    reliably crashes its worker (poison pill) must converge to the DLQ
    instead of cycling through the fleet forever.
    """
    if attempt_number >= policy.max_attempts:
        return FailureDecision(retry=False, delay_seconds=0.0)
    return FailureDecision(
        retry=True,
        delay_seconds=compute_backoff_seconds(policy, attempt_number, rng),
    )
