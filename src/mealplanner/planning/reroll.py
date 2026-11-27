"""Replace one option of the slate (ADR 0019).

The replacement goes through the same pipeline as generation (``fill_options``): schema, code
checks, same-meal rule, one call plus at most two top-ups. Nothing changes unless it succeeds.
"""

import sqlite3
from datetime import datetime

from mealplanner.llm.client import LLMClient
from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.errors import OptionNotFoundError, ProfileRequiredError
from mealplanner.planning.generate import fill_options
from mealplanner.planning.models import SlateView
from mealplanner.planning.prompt import build_system_prompt
from mealplanner.planning.selection import refuse_if_not_replaceable
from mealplanner.planning.store import (
    get_active_option,
    get_slate,
    recent_meals,
    replace_option,
    replaced_drafts,
)
from mealplanner.planning.view import load_slate_view
from mealplanner.profile.store import get_current_profile


def reroll_option(
    conn: sqlite3.Connection,
    llm: LLMClient,
    catalog: Catalog,
    user_id: int,
    option_id: int,
    *,
    now: datetime,
) -> SlateView:
    """Replace the option, keep the old row as history, and return the whole slate."""
    ref = get_active_option(conn, user_id, option_id)
    if ref is None:
        raise OptionNotFoundError(f"option {option_id} not found")
    refuse_if_not_replaceable(conn, user_id, ref.slate_id, option_id)
    current = get_current_profile(conn, user_id)
    if current is None:
        raise ProfileRequiredError("save a profile before replacing an option")
    profile = current.profile

    slate = get_slate(conn, user_id, ref.week_start)
    assert slate is not None
    # Every active option (both types, the one being replaced included) and every earlier one.
    earlier = [o.draft for group in slate.options.values() for o in group]
    earlier += replaced_drafts(conn, user_id, ref.slate_id)
    recent = recent_meals(conn, user_id, ref.week_start, profile.repeat_after_weeks)

    (replacement,) = fill_options(
        llm,
        catalog,
        build_system_prompt(catalog, profile.exclusions),
        profile,
        ref.option.meal_type,
        1,
        recent,
        earlier,
    )
    # The model call took time: a pick or confirmation may have landed meanwhile.
    refuse_if_not_replaceable(conn, user_id, ref.slate_id, option_id)
    replace_option(conn, user_id, option_id, replacement, now=now)
    view = load_slate_view(conn, catalog, user_id, ref.week_start)
    assert view is not None
    return view
