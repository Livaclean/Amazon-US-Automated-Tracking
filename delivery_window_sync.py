# delivery_window_sync.py
"""
Keeps a shipment's Amazon "Send to Amazon" delivery window in sync with its
real carrier-reported expected delivery date: if the two disagree, moves the
window to cover the real date; if there's no real date yet and the window is
about to lock (Amazon stops allowing edits once a window's start date
arrives), pushes it two weeks out to buy time rather than let it lock on a
guess. Only acts on shipments not already marked "Delivered" in the master
sheet, and skips windows that have already locked.
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_DATE_FORMATS_WITH_YEAR = [
    "%m/%d/%Y", "%m/%d/%y",
    "%Y-%m-%d",
    "%b %d, %Y", "%B %d, %Y",
    "%b %d %Y", "%B %d %Y",
]
_DATE_FORMATS_NO_YEAR = [
    "%A, %B %d", "%a, %b %d",
    "%B %d", "%b %d",
]

# If a no-year date (e.g. "Jan 5") would land more than this many days in the
# past under the current year, it almost certainly means next year instead.
_NO_YEAR_PAST_ROLLOVER_DAYS = 60


def _parse_flexible_date(date_str, today=None):
    """
    Parses a date string in any of the formats carrier pages/APIs have been
    observed to return (see tracking_status.py's _DATE_PATTERN and the FedEx
    API's displayActDeliveryDt/displayEstDeliveryDt fields). Returns a
    datetime.date, or None if date_str is blank or unparseable.

    Formats with no year (e.g. "Friday, July 17", from UPS's delivered banner)
    are assumed to be in `today`'s year, rolling to next year if that would
    place the date more than ~2 months in the past -- a bare month/day this
    far behind almost certainly means the carrier meant next year.
    """
    if not date_str:
        return None
    date_str = str(date_str).strip()
    if not date_str:
        return None
    if today is None:
        today = datetime.now().date()

    for fmt in _DATE_FORMATS_WITH_YEAR:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    for fmt in _DATE_FORMATS_NO_YEAR:
        try:
            # Parse with an explicit placeholder year (1904, a leap year so
            # "Feb 29" doesn't fail either) instead of a year-less format --
            # Python 3.14+ deprecates strptime with no year in the format at
            # all. The real year is inferred and substituted right after.
            parsed = datetime.strptime(f"{date_str} 1904", f"{fmt} %Y")
        except ValueError:
            continue
        candidate = parsed.replace(year=today.year).date()
        if (today - candidate).days > _NO_YEAR_PAST_ROLLOVER_DAYS:
            candidate = candidate.replace(year=today.year + 1)
        return candidate

    return None


def _week_bounds(d):
    """Returns (Sunday, Saturday) of the calendar week containing date d -- matches
    the calendar-week convention Amazon's delivery windows always use."""
    days_since_sunday = (d.weekday() + 1) % 7  # Python: Monday=0 .. Sunday=6
    sunday = d - timedelta(days=days_since_sunday)
    saturday = sunday + timedelta(days=6)
    return sunday, saturday


def decide_window_action(window_start, window_end, expected_delivery_date, today) -> dict:
    """
    Pure decision, no I/O: given a shipment's current Amazon delivery window,
    its real expected delivery date (a date, or None if not known yet), and
    today's date, decides what to do. Returns
    {"action": "locked" | "none" | "edit" | "push_two_weeks", "target_week_start": date | None}.

    - "locked": the window's start date has already arrived. Amazon's own
      edit cutoff always equals the window's start date (confirmed against
      several real windows), so nothing can be done via this UI anymore.
    - "none": the expected date already falls within the current window, or
      there's no expected date yet and the window isn't starting soon enough
      to need a defensive push.
    - "edit": the expected date is known and falls outside the window -- move
      the window to the calendar week containing it.
    - "push_two_weeks": no expected date yet, and the window starts within
      the next 7 days (about to lock) -- push it out two weeks so it doesn't
      lock on a guess while we wait for a real date.
    """
    if today >= window_start:
        return {"action": "locked", "target_week_start": None}

    if expected_delivery_date is not None:
        if window_start <= expected_delivery_date <= window_end:
            return {"action": "none", "target_week_start": None}
        target_start, _ = _week_bounds(expected_delivery_date)
        return {"action": "edit", "target_week_start": target_start}

    if (window_start - today).days <= 7:
        target_start, _ = _week_bounds(today + timedelta(days=14))
        return {"action": "push_two_weeks", "target_week_start": target_start}

    return {"action": "none", "target_week_start": None}
