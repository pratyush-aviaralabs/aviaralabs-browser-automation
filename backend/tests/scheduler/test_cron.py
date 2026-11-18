from datetime import datetime

import pytest

from dealercenter_agent.scheduler.cron import build_cron_expression, next_fire_time

# 2026-11-18 is a Wednesday (weekday()==2, Mon=0..Sun=6 — same
# convention this module uses for day_of_week).
WEDNESDAY = datetime(2026, 11, 18, 10, 0, 0)


def test_build_cron_expression_for_hour_only_uses_minute():
    assert build_cron_expression("HOUR", "00:15", None, None) == "15 * * * *"


def test_build_cron_expression_for_day():
    assert build_cron_expression("DAY", "09:30", None, None) == "30 9 * * *"


def test_build_cron_expression_for_week_maps_day_of_week_to_cron_convention():
    # Monday (our 0) -> cron 1; Sunday (our 6) -> cron 0.
    assert build_cron_expression("WEEK", "08:00", 0, None) == "0 8 * * 1"
    assert build_cron_expression("WEEK", "08:00", 6, None) == "0 8 * * 0"


def test_build_cron_expression_for_month():
    assert build_cron_expression("MONTH", "06:00", None, 15) == "0 6 15 * *"


def test_build_cron_expression_week_requires_day_of_week():
    with pytest.raises(ValueError, match="day_of_week is required"):
        build_cron_expression("WEEK", "08:00", None, None)


def test_build_cron_expression_month_requires_day_of_month():
    with pytest.raises(ValueError, match="day_of_month is required"):
        build_cron_expression("MONTH", "06:00", None, None)


def test_build_cron_expression_rejects_unknown_frequency():
    with pytest.raises(ValueError, match="unknown frequency_unit"):
        build_cron_expression("YEAR", "06:00", None, None)


def test_next_fire_time_hourly_rolls_to_the_next_hour_mark():
    # after=10:00, fire at :15 past every hour -> next is 10:15 same day.
    result = next_fire_time("HOUR", "00:15", None, None, after=WEDNESDAY)
    assert result == datetime(2026, 11, 18, 10, 15)


def test_next_fire_time_daily_rolls_to_tomorrow_if_todays_time_passed():
    # after=10:00, fire at 09:00 daily -> already passed today, next is tomorrow.
    result = next_fire_time("DAY", "09:00", None, None, after=WEDNESDAY)
    assert result == datetime(2026, 11, 19, 9, 0)


def test_next_fire_time_daily_stays_today_if_time_has_not_passed():
    # after=10:00, fire at 17:00 daily -> still today.
    result = next_fire_time("DAY", "17:00", None, None, after=WEDNESDAY)
    assert result == datetime(2026, 11, 18, 17, 0)


def test_next_fire_time_weekly_finds_the_right_upcoming_weekday():
    # Wednesday 10:00 -> next Friday (our day_of_week=4) at 08:00.
    result = next_fire_time("WEEK", "08:00", 4, None, after=WEDNESDAY)
    assert result == datetime(2026, 11, 20, 8, 0)
    assert result.strftime("%A") == "Friday"


def test_next_fire_time_monthly_finds_the_right_upcoming_day():
    # Nov 18 -> fire on the 1st -> rolls to December 1st.
    result = next_fire_time("MONTH", "06:00", None, 1, after=WEDNESDAY)
    assert result == datetime(2026, 12, 1, 6, 0)


def test_next_fire_time_respects_timezone():
    # 09:00 in America/New_York (UTC-5 in November, EST) is 14:00 UTC.
    result = next_fire_time("DAY", "09:00", None, None, after=WEDNESDAY, tz_name="America/New_York")
    assert result == datetime(2026, 11, 18, 14, 0)
