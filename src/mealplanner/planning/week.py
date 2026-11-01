"""Which week a slate belongs to (ADR 0019)."""

from datetime import date, timedelta


def week_start_for(today: date) -> date:
    """The Monday on or after ``today``."""
    return today + timedelta(days=(-today.weekday()) % 7)
