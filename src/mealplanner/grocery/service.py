"""The grocery page's data, derived on read from the confirmed plan (ADR 0024).

Makes no model call and writes nothing. The meals come only from ``get_confirmed_plan``
(ADR 0021); nothing is built while the week is open or any planned meal no longer fits.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

from mealplanner.grocery.build import build_grocery_list
from mealplanner.grocery.info import GroceryInfo
from mealplanner.grocery.models import GroceryList
from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.errors import ProfileRequiredError
from mealplanner.planning.selection import get_confirmed_plan
from mealplanner.profile.store import get_current_profile


@dataclass(frozen=True)
class WeekOpen:
    """No confirmed plan: never confirmed, or reopened."""


@dataclass(frozen=True)
class MealNoLongerFits:
    """A planned meal fails the current profile, so no list is shown (P9)."""


@dataclass(frozen=True)
class NoMealsPlanned:
    """The week was confirmed with no meals."""


@dataclass(frozen=True)
class Ready:
    list: GroceryList


GroceryPage = WeekOpen | MealNoLongerFits | NoMealsPlanned | Ready


def load_grocery_page(
    conn: sqlite3.Connection,
    catalog: Catalog,
    grocery_info: GroceryInfo,
    user_id: int,
    week_start: date,
) -> GroceryPage:
    current = get_current_profile(conn, user_id)
    if current is None:
        raise ProfileRequiredError("a grocery list needs a saved profile")
    plan = get_confirmed_plan(conn, catalog, user_id, week_start)
    if plan is None:
        return WeekOpen()
    if plan.unavailable:
        return MealNoLongerFits()
    if not plan.meals:
        return NoMealsPlanned()
    return Ready(
        build_grocery_list(
            plan.meals,
            catalog=catalog,
            grocery_info=grocery_info,
            servings=current.profile.servings,
        )
    )
