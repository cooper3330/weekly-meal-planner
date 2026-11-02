from datetime import date

import pytest

from mealplanner.planning.week import week_start_for


def test_week_start_for_monday_maps_to_itself() -> None:
    assert week_start_for(date(2026, 11, 2)) == date(2026, 11, 2)


@pytest.mark.parametrize("day", range(3, 9))
def test_week_start_for_tuesday_to_sunday_is_next_monday(day: int) -> None:
    assert week_start_for(date(2026, 11, day)) == date(2026, 11, 9)


def test_week_start_for_year_change() -> None:
    assert week_start_for(date(2026, 12, 29)) == date(2027, 1, 4)
