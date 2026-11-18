"""Translates a SchedulerOccurrence's structured fields (frequency
unit, time of day, day of week/month) into a standard 5-field cron
expression and computes next-fire times via croniter — calendar math
(month-end days, DST, leap years) is exactly the kind of thing a
battle-tested library should own, not something hand-rolled here."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from ..utils.timezones import to_utc

FREQUENCY_UNITS = ("HOUR", "DAY", "WEEK", "MONTH")


def _cron_day_of_week(day_of_week: int) -> int:
    """Our day_of_week is 0=Mon..6=Sun; cron's is 0=Sun..6=Sat."""
    return (day_of_week + 1) % 7


def build_cron_expression(
    frequency_unit: str,
    time_of_day: str,
    day_of_week: int | None,
    day_of_month: int | None,
) -> str:
    hour_str, minute_str = time_of_day.split(":")
    hour, minute = int(hour_str), int(minute_str)

    if frequency_unit == "HOUR":
        # Only the minute matters for hourly — fire at :MM past every hour.
        return f"{minute} * * * *"
    if frequency_unit == "DAY":
        return f"{minute} {hour} * * *"
    if frequency_unit == "WEEK":
        if day_of_week is None:
            raise ValueError("day_of_week is required for a WEEK-frequency occurrence")
        return f"{minute} {hour} * * {_cron_day_of_week(day_of_week)}"
    if frequency_unit == "MONTH":
        if day_of_month is None:
            raise ValueError("day_of_month is required for a MONTH-frequency occurrence")
        return f"{minute} {hour} {day_of_month} * *"
    raise ValueError(f"unknown frequency_unit: {frequency_unit!r} (expected one of {FREQUENCY_UNITS})")


def next_fire_time(
    frequency_unit: str,
    time_of_day: str,
    day_of_week: int | None,
    day_of_month: int | None,
    after: datetime,
    tz_name: str = "UTC",
) -> datetime:
    """Returns the next fire time strictly after `after`, as a naive
    UTC datetime — matching how every other timestamp in this app is
    stored (see _utcnow() in db/models.py)."""
    cron_expr = build_cron_expression(frequency_unit, time_of_day, day_of_week, day_of_month)
    tz = ZoneInfo(tz_name)
    after_utc = after if after.tzinfo is not None else after.replace(tzinfo=timezone.utc)
    after_in_tz = after_utc.astimezone(tz)
    next_dt = croniter(cron_expr, after_in_tz).get_next(datetime)
    return to_utc(next_dt, tz_name)
