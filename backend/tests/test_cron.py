"""Unit tests for the pure cron domain logic."""

import datetime as dt

import pytest

from app.domain.cron import InvalidCronExpression, next_run_after, validate_cron

UTC = dt.timezone.utc


class TestValidate:
    def test_accepts_standard_expressions(self):
        for expr in ["* * * * *", "*/5 * * * *", "0 0 * * 0", "15 2,14 1-7 * 1-5"]:
            assert validate_cron(expr) == expr

    def test_rejects_garbage(self):
        for expr in ["", "not cron", "61 * * * *", "* * * *"]:
            with pytest.raises(InvalidCronExpression):
                validate_cron(expr)


class TestNextRunAfter:
    def test_next_minute_boundary(self):
        after = dt.datetime(2026, 7, 4, 12, 0, 30, tzinfo=UTC)
        assert next_run_after("* * * * *", after) == dt.datetime(
            2026, 7, 4, 12, 1, tzinfo=UTC
        )

    def test_strictly_after(self):
        # Sitting exactly on a tick must yield the *next* tick, or a
        # schedule whose cursor equals a boundary would fire twice.
        after = dt.datetime(2026, 7, 4, 12, 5, 0, tzinfo=UTC)
        assert next_run_after("*/5 * * * *", after) == dt.datetime(
            2026, 7, 4, 12, 10, tzinfo=UTC
        )

    def test_daily_schedule_crosses_midnight(self):
        after = dt.datetime(2026, 7, 4, 23, 59, tzinfo=UTC)
        assert next_run_after("30 0 * * *", after) == dt.datetime(
            2026, 7, 5, 0, 30, tzinfo=UTC
        )

    def test_requires_timezone_aware_input(self):
        with pytest.raises(ValueError):
            next_run_after("* * * * *", dt.datetime(2026, 7, 4, 12, 0))
