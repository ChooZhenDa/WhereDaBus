"""Inclusive Singapore date selections for historical reports (at most 31 days)."""
from datetime import date, datetime, time, timedelta, timezone
from .analytics import SG, previous_seven_days


def analytics_window(date_from: date | None = None, date_to: date | None = None,
                     now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Current time must include a timezone.')
    if date_from is None and date_to is None:
        return previous_seven_days(now)
    if date_from is None or date_to is None:
        raise ValueError('Choose both a start date and an end date.')
    if date_from > date_to:
        raise ValueError('The start date must be on or before the end date.')
    if (date_to - date_from).days >= 31:
        raise ValueError('Choose a date range of at most 31 calendar days.')
    if date_to > now.astimezone(SG).date():
        raise ValueError('The end date cannot be in the future in Singapore.')
    start = datetime.combine(date_from, time.min, SG).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, SG).astimezone(timezone.utc)
    end = min(end, now.astimezone(timezone.utc))
    if start >= end:
        raise ValueError('This date range has not started yet in Singapore.')
    return start, end
