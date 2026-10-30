"""Time handling. Asia/Kolkata has no daylight saving, so naive IST wall-clock
times stored in the database are unambiguous. Anything leaving the API carries an
explicit +05:30 offset; anything arriving with an offset is converted to IST first."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .config import IST


def now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST).replace(tzinfo=None, microsecond=0)


def to_ist_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(IST).replace(tzinfo=None)


def with_ist(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt.replace(tzinfo=IST)


def iso(dt: datetime | None) -> str | None:
    """Naive IST wall time -> ISO string with +05:30."""
    return None if dt is None else with_ist(dt).isoformat()


def combine(d: date, t: time) -> datetime:
    return datetime.combine(d, t)


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min)
    return start, start + timedelta(days=1)


def minutes_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


def hhmm(t: time) -> str:
    return t.strftime("%H:%M")
