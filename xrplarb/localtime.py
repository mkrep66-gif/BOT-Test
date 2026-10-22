"""Your local time. The logbook stores every time in UTC; "today", the daily
history and the daily loss cap all follow your own midnight instead.

The time zone comes from `timezone:` in config/settings.yaml (e.g.
"Europe/London"); empty means this computer's own time zone.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional

_tz_name = ""


def set_timezone(name: str) -> None:
    global _tz_name
    _tz_name = name or ""


def local_tz() -> tzinfo:
    if _tz_name:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_tz_name)
    return datetime.now().astimezone().tzinfo or timezone.utc


def tz_label() -> str:
    return _tz_name or (datetime.now(local_tz()).tzname() or "local time")


def today_local() -> str:
    return datetime.now(local_tz()).strftime("%Y-%m-%d")


def day_start_utc(day: str) -> str:
    """Your local midnight at the start of `day` (YYYY-MM-DD), as a UTC
    timestamp in the logbook's format, for comparing against stored times."""
    d = date.fromisoformat(day)
    midnight = datetime(d.year, d.month, d.day, tzinfo=local_tz())
    return midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def to_local(iso_utc: Optional[str]) -> Optional[datetime]:
    """Stored UTC timestamp -> your local time."""
    if not iso_utc:
        return None
    dt = datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz())


def fmt_local(iso_utc: Optional[str], with_date: bool = True) -> str:
    dt = to_local(iso_utc)
    if dt is None:
        return ""
    return dt.strftime("%m-%d %H:%M:%S" if with_date else "%H:%M:%S")
