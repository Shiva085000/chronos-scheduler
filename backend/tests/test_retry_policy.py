"""Unit tests for the pure retry-policy domain logic."""

import random

import pytest

from app.domain.retry import RetryPolicy, compute_backoff_seconds, decide_failure

POLICY = RetryPolicy(
    max_attempts=3,
    backoff_base_seconds=5,
    backoff_factor=2.0,
    backoff_max_seconds=300,
)


def fixed_rng(value: float) -> random.Random:
    rng = random.Random()
    rng.random = lambda: value  # type: ignore[method-assign]
    return rng


class TestComputeBackoff:
    def test_exponential_growth_without_jitter(self):
        rng = fixed_rng(0.0)
        assert compute_backoff_seconds(POLICY, 1, rng) == 5.0
        assert compute_backoff_seconds(POLICY, 2, rng) == 10.0
        assert compute_backoff_seconds(POLICY, 3, rng) == 20.0

    def test_cap_is_enforced(self):
        rng = fixed_rng(0.0)
        assert compute_backoff_seconds(POLICY, 10, rng) == 300.0

    def test_jitter_adds_at_most_twenty_percent(self):
        rng = fixed_rng(1.0)
        assert compute_backoff_seconds(POLICY, 1, rng) == pytest.approx(6.0)

    def test_rejects_invalid_attempt(self):
        with pytest.raises(ValueError):
            compute_backoff_seconds(POLICY, 0)


class TestDecideFailure:
    def test_retries_while_attempts_remain(self):
        decision = decide_failure(POLICY, 1, fixed_rng(0.0))
        assert decision.retry is True
        assert decision.delay_seconds == 5.0

    def test_backoff_grows_with_attempt_number(self):
        first = decide_failure(POLICY, 1, fixed_rng(0.0))
        second = decide_failure(POLICY, 2, fixed_rng(0.0))
        assert second.delay_seconds > first.delay_seconds

    def test_exhausted_budget_goes_to_dlq(self):
        decision = decide_failure(POLICY, 3)
        assert decision.retry is False
        assert decision.delay_seconds == 0.0

    def test_single_attempt_policy_never_retries(self):
        policy = RetryPolicy(
            max_attempts=1,
            backoff_base_seconds=5,
            backoff_factor=2.0,
            backoff_max_seconds=300,
        )
        assert decide_failure(policy, 1).retry is False
