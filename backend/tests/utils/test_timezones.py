from datetime import datetime, timezone

from dealercenter_agent.utils.timezones import US_TIMEZONES, is_valid_timezone, to_utc


def test_us_timezones_includes_the_major_zones():
    values = {tz["value"] for tz in US_TIMEZONES}
    assert "America/New_York" in values
    assert "America/Los_Angeles" in values
    assert "Pacific/Honolulu" in values


def test_us_timezones_have_readable_labels():
    by_value = {tz["value"]: tz["label"] for tz in US_TIMEZONES}
    assert by_value["America/New_York"] == "New York"
    assert by_value["America/Indiana/Indianapolis"] == "Indiana - Indianapolis"


def test_is_valid_timezone_accepts_known_zone():
    assert is_valid_timezone("America/New_York") is True
    assert is_valid_timezone("UTC") is True


def test_is_valid_timezone_rejects_garbage():
    assert is_valid_timezone("Not/AZone") is False
    assert is_valid_timezone("") is False


def test_to_utc_converts_naive_local_time():
    # 09:00 in America/New_York (EST, UTC-5 in November) -> 14:00 UTC.
    result = to_utc(datetime(2026, 11, 18, 9, 0, 0), "America/New_York")
    assert result == datetime(2026, 11, 18, 14, 0, 0)


def test_to_utc_converts_aware_datetime_using_its_own_tzinfo():
    aware = datetime(2026, 11, 18, 14, 0, 0, tzinfo=timezone.utc)
    result = to_utc(aware, "America/New_York")
    assert result == datetime(2026, 11, 18, 14, 0, 0)


def test_to_utc_returns_naive_result():
    result = to_utc(datetime(2026, 11, 18, 9, 0, 0), "UTC")
    assert result.tzinfo is None
