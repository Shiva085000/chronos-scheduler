"""Cron expression handling — thin, pure wrapper over croniter.

All schedule times are UTC by design: a job system's "when" must be
unambiguous across the fleet, and per-schedule timezones (DST jumps,
ambiguous local times) buy convenience at the price of exactly the class
of subtle bugs this project exists to avoid. Clients localize for display.
"""

import datetime as dt

from croniter import CroniterBadCronError, croniter


class InvalidCronExpression(ValueError):
    pass


def validate_cron(expr: str) -> str:
    """Return the expression if valid, raise InvalidCronExpression if not.

    Standard 5-field cron syntax (minute hour day month weekday),
    including ranges, steps and lists — e.g. "*/5 * * * *".
    """
    try:
        croniter(expr)
    except (CroniterBadCronError, ValueError) as exc:
        raise InvalidCronExpression(f"invalid cron expression {expr!r}: {exc}") from None
    return expr


def next_run_after(expr: str, after: dt.datetime) -> dt.datetime:
    """First fire time strictly after `after` (must be timezone-aware UTC)."""
    if after.tzinfo is None:
        raise ValueError("`after` must be timezone-aware")
    return croniter(expr, after).get_next(dt.datetime)
