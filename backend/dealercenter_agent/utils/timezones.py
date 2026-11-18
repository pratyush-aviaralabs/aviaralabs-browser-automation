"""Generic local-time/any-IANA-zone -> UTC conversion, shared by every
API that accepts a timezone from the client (schedulers today, anything
else later) — everything in the DB is stored as naive UTC (see
_utcnow() in db/models.py), so inbound local times all funnel through
here before they're persisted or compared.

US_TIMEZONES comes from pytz's IANA-derived country table rather than a
hand-maintained list, so it stays correct without upkeep."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz


def _label_for(tz_name: str) -> str:
    # "America/Indiana/Indianapolis" -> "Indiana - Indianapolis"
    # "America/New_York" -> "New York"
    parts = tz_name.split("/")[1:]
    return " - ".join(part.replace("_", " ") for part in parts)


US_TIMEZONES = [
    {"value": tz_name, "label": _label_for(tz_name)}
    for tz_name in sorted(pytz.country_timezones["US"])
]


def is_valid_timezone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def to_utc(dt: datetime, tz_name: str) -> datetime:
    """Interprets `dt` as local time in `tz_name` (if naive) or converts
    it from its own tzinfo, and returns a naive UTC datetime."""
    tz = ZoneInfo(tz_name)
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=tz)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)
