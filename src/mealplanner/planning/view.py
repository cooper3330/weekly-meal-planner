"""Read a stored slate back as something that may be shown (ADR 0019).

Nutrition is not stored. Every active option is rebuilt through ``check_option`` with the
bundled catalog and the user's current profile, so the numbers are always computed in code
and a newly added exclusion hides a conflicting option at once.
"""

import sqlite3
from dataclasses import replace
from datetime import date

from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.checks import check_option
from mealplanner.planning.errors import ProfileRequiredError
from mealplanner.planning.models import MealOption, MealType, SlateView, Slot, UnavailableOption
from mealplanner.planning.store import StoredOption, get_slate
from mealplanner.profile.models import Profile
from mealplanner.profile.store import get_current_profile


def _slot(stored: StoredOption, catalog: Catalog, profile: Profile) -> Slot:
    result = check_option(
        stored.draft,
        catalog=catalog,
        servings=stored.servings,
        max_cook_minutes=profile.max_cook_minutes,
        exclusions=profile.exclusions,
    )
    if isinstance(result, MealOption):
        return replace(result, option_id=stored.id)
    return UnavailableOption(option_id=stored.id, reason=result.reason)


def load_slate_view(
    conn: sqlite3.Connection, catalog: Catalog, user_id: int, week_start: date
) -> SlateView | None:
    """The user's slate for the week with every slot re-checked, or None if there is none."""
    stored = get_slate(conn, user_id, week_start)
    if stored is None:
        return None
    current = get_current_profile(conn, user_id)
    if current is None:
        raise ProfileRequiredError("a slate exists but the user has no saved profile")
    return SlateView(
        slate_id=stored.id,
        week_start=stored.week_start,
        profile_version=stored.profile_version,
        options={
            mt: tuple(_slot(o, catalog, current.profile) for o in stored.options[mt])
            for mt in MealType
        },
    )
