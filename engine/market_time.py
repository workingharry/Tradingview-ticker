"""US Stock Market Session & Time Utilities.

Provides timezone-aware market session calculations based on America/New_York.
Supports injecting custom `now` datetimes for offline deterministic testing.

Known limitations:
- Does not handle US stock market holidays (e.g. Thanksgiving 2026-11-26, Christmas, Good Friday, etc.).
  Session determinations are based strictly on Monday through Friday calendar days and regular trading hours.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
import zoneinfo

NY_TZ = zoneinfo.ZoneInfo("America/New_York")


def ny_now(now: Optional[datetime] = None) -> datetime:
    """Converts input datetime to America/New_York timezone.
    
    If `now` is None, current UTC time is converted.
    If `now` is timezone-naive, it is assumed to be in UTC.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(NY_TZ)


def is_regular_session_open(now: Optional[datetime] = None) -> bool:
    """Returns True if input time is within US regular market hours (Mon-Fri 09:30-16:00 ET)."""
    t = ny_now(now)
    # Monday is 0, Friday is 4
    if not (0 <= t.weekday() <= 4):
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= t.time() < market_close


def current_session_date(now: Optional[datetime] = None) -> date:
    """Returns the US trading session date that current market data belongs to.

    Rules:
    - Monday to Friday after 09:30 ET: returns current date.
    - Monday to Friday before 09:30 ET: returns the previous Monday-Friday trading day.
    - Weekend (Saturday or Sunday): returns the previous Friday.
    """
    t = ny_now(now)
    wd = t.weekday()
    d = t.date()

    if wd <= 4:
        if t.time() >= time(9, 30):
            return d
        else:
            # Before 09:30 ET on a weekday: step back to previous weekday
            if wd == 0:  # Monday -> Friday
                return d - timedelta(days=3)
            else:
                return d - timedelta(days=1)
    elif wd == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    else:  # Sunday -> Friday
        return d - timedelta(days=2)


def last_completed_session_date(now: Optional[datetime] = None) -> date:
    """Returns the most recent completed (closed after 16:00 ET) trading session date.

    Rules:
    - Monday to Friday after 16:00 ET: returns current date.
    - Monday to Friday before 16:00 ET: returns the previous Monday-Friday trading day.
    - Weekend: returns the previous Friday.
    """
    t = ny_now(now)
    wd = t.weekday()
    d = t.date()

    if wd <= 4:
        if t.time() >= time(16, 0):
            return d
        else:
            if wd == 0:  # Monday -> Friday
                return d - timedelta(days=3)
            else:
                return d - timedelta(days=1)
    elif wd == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    else:  # Sunday -> Friday
        return d - timedelta(days=2)
