"""Unit tests for the pure retry-policy domain logic."""

import random

import pytest

from app.domain.retry import (
    RetryPolicy,
    RetryStrategy,
    compute_backoff_seconds,
    decide_failure,
)

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


class TestStrategies:
    def _policy(self, strategy: RetryStrategy) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=10,
            backoff_base_seconds=5,
            backoff_factor=2.0,
            backoff_max_seconds=300,
            strategy=strategy,
        )

    def test_fixed_delay_is_constant(self):
        policy = self._policy(RetryStrategy.FIXED)
        rng = fixed_rng(0.0)
        assert compute_backoff_seconds(policy, 1, rng) == 5.0
        assert compute_backoff_seconds(policy, 4, rng) == 5.0
        assert compute_backoff_seconds(policy, 9, rng) == 5.0

    def test_linear_grows_with_attempt_number(self):
        policy = self._policy(RetryStrategy.LINEAR)
        rng = fixed_rng(0.0)
        assert compute_backoff_seconds(policy, 1, rng) == 5.0
        assert compute_backoff_seconds(policy, 2, rng) == 10.0
        assert compute_backoff_seconds(policy, 3, rng) == 15.0

    def test_linear_respects_cap(self):
        policy = self._policy(RetryStrategy.LINEAR)
        assert compute_backoff_seconds(policy, 100, fixed_rng(0.0)) == 300.0

    def test_default_strategy_is_exponential(self):
        assert POLICY.strategy is RetryStrategy.EXPONENTIAL

    def test_factor_ignored_outside_exponential(self):
        fixed_hi_factor = RetryPolicy(
            max_attempts=10,
            backoff_base_seconds=5,
            backoff_factor=9.0,
            backoff_max_seconds=300,
            strategy=RetryStrategy.FIXED,
        )
        assert compute_backoff_seconds(fixed_hi_factor, 5, fixed_rng(0.0)) == 5.0

    def test_jitter_applies_to_all_strategies(self):
        for strategy in RetryStrategy:
            policy = self._policy(strategy)
            base = compute_backoff_seconds(policy, 1, fixed_rng(0.0))
            jittered = compute_backoff_seconds(policy, 1, fixed_rng(1.0))
            assert jittered == pytest.approx(base * 1.2)
