"""New York time helpers for anything that needs hours, not days (same-day expiries)."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
EXPIRY_TIME = time(16, 0)  # US equity/ETF options stop trading at 4pm New York
YEAR_S = 365 * 24 * 3600


def now_ny() -> datetime:
    return datetime.now(NY)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, NY)


def years_between(start: datetime, end: datetime, floor_s: float = 60.0) -> float:
    """Time from start to end in years (for option pricing), never below `floor_s` seconds."""
    return max((end - start).total_seconds(), floor_s) / YEAR_S


def years_to_expiry(expiry: date, now: datetime | None = None) -> float:
    return years_between(now or now_ny(), at(expiry, EXPIRY_TIME))


def early_close(day: date) -> bool:
    """US market half-days (1pm close): July 3, the day after Thanksgiving, Christmas Eve.
    Approximate: ignores years when July 3 is itself the observed holiday."""
    if day.weekday() >= 5:
        return False
    if (day.month, day.day) in {(7, 3), (12, 24)}:
        return True
    if day.month == 11 and day.weekday() == 4:  # Friday after the 4th Thursday
        return 22 <= day.day - 1 <= 28
    return False
