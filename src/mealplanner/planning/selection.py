"""Draft picks, limits, the nutrition summary, and confirming the week (ADR 0021).

Plain functions with an explicit connection and user id (ADR 0011). Nothing here calls the
model, and nothing stores nutrition: every number comes from ``load_slate_view``.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.compute import Nutrition
from mealplanner.planning.checks import check_option
from mealplanner.planning.errors import (
    OptionNotFoundError,
    OptionUnavailableError,
    ProfileRequiredError,
    SelectionLimitError,
    SelectionLockedError,
    SelectionNotConfirmedError,
    SlateNotFoundError,
    UnavailablePickError,
)
from mealplanner.planning.models import (
    ConfirmedPlan,
    MealOption,
    MealType,
    NutritionSummary,
    SelectionItem,
    SelectionView,
    UnavailableOption,
)
from mealplanner.planning.schema import MealOptionDraft
from mealplanner.planning.store import _timestamp, get_active_option, get_slate
from mealplanner.planning.view import load_slate_view
from mealplanner.profile.store import get_current_profile

DEFAULT_LIMIT = 5
MIN_LIMIT = 0
MAX_LIMIT = 14

_PLURAL = {MealType.lunch: "lunches", MealType.dinner: "dinners"}
_ZERO = Nutrition(0.0, 0.0, 0.0, 0.0)


def summarize(options: Sequence[MealOption]) -> NutritionSummary:
    """Sum and mean of the unrounded per-serving nutrition. Rounding is left to display."""
    count = len(options)
    if count == 0:
        return NutritionSummary(count=0, total=_ZERO, average=_ZERO)
    total = Nutrition(
        kcal=sum(o.nutrition.kcal for o in options),
        protein_g=sum(o.nutrition.protein_g for o in options),
        carbs_g=sum(o.nutrition.carbs_g for o in options),
        fat_g=sum(o.nutrition.fat_g for o in options),
    )
    average = Nutrition(
        kcal=total.kcal / count,
        protein_g=total.protein_g / count,
        carbs_g=total.carbs_g / count,
        fat_g=total.fat_g / count,
    )
    return NutritionSummary(count=count, total=total, average=average)


def _limits(conn: sqlite3.Connection, slate_id: int) -> tuple[int, int]:
    row = conn.execute(
        "SELECT lunch_limit, dinner_limit FROM selections WHERE slate_id = ?", (slate_id,)
    ).fetchone()
    if row is None:
        return DEFAULT_LIMIT, DEFAULT_LIMIT
    return int(row["lunch_limit"]), int(row["dinner_limit"])


def _picked_ids(conn: sqlite3.Connection, user_id: int, slate_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT p.slate_option_id FROM selection_picks p "
        "JOIN selections s ON s.id = p.selection_id "
        "WHERE s.slate_id = ? AND s.user_id = ? AND p.user_id = ?",
        (slate_id, user_id, user_id),
    ).fetchall()
    return {int(r["slate_option_id"]) for r in rows}


def load_selection_view(
    conn: sqlite3.Connection, catalog: Catalog, user_id: int, week_start: date
) -> SelectionView | None:
    """The slate with picks, limits and the summary of the available picks; None without a slate.

    Reading never writes: without a ``selections`` row the defaults apply.
    """
    slate = load_slate_view(conn, catalog, user_id, week_start)
    if slate is None:
        return None
    picked = _picked_ids(conn, user_id, slate.slate_id)
    lunch_limit, dinner_limit = _limits(conn, slate.slate_id)
    items = {
        mt: tuple(SelectionItem(slot=s, picked=s.option_id in picked) for s in slate.options[mt])
        for mt in MealType
    }
    chosen = [
        i.slot
        for group in items.values()
        for i in group
        if i.picked and isinstance(i.slot, MealOption)
    ]
    confirmation_id = _current_confirmation_id(conn, user_id, slate.slate_id)
    confirmed_unavailable: tuple[UnavailableOption, ...] = ()
    if confirmation_id is not None:
        current_plan = get_confirmed_plan(conn, catalog, user_id, week_start)
        if current_plan is not None:
            confirmed_unavailable = tuple(current_plan.unavailable)
    return SelectionView(
        slate_id=slate.slate_id,
        week_start=slate.week_start,
        lunch_limit=lunch_limit,
        dinner_limit=dinner_limit,
        items=items,
        pick_counts={mt: sum(1 for i in items[mt] if i.picked) for mt in MealType},
        summary=summarize(chosen),
        confirmed=confirmation_id is not None,
        confirmation_id=confirmation_id,
        confirmed_unavailable=confirmed_unavailable,
    )


def _current_confirmation_id(conn: sqlite3.Connection, user_id: int, slate_id: int) -> int | None:
    """The slate's latest confirmation, if it has not been reopened (the week is confirmed)."""
    row = conn.execute(
        "SELECT c.id, c.reopened_at FROM selection_confirmations c "
        "JOIN selections s ON s.id = c.selection_id "
        "WHERE s.slate_id = ? AND s.user_id = ? AND c.user_id = ? ORDER BY c.id DESC LIMIT 1",
        (slate_id, user_id, user_id),
    ).fetchone()
    if row is None or row["reopened_at"] is not None:
        return None
    return int(row["id"])


def _refuse_if_confirmed(conn: sqlite3.Connection, user_id: int, slate_id: int) -> None:
    if _current_confirmation_id(conn, user_id, slate_id) is not None:
        raise SelectionLockedError("This week's plan is confirmed. Reopen it to make changes.")


def _begin(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _finish(conn: sqlite3.Connection) -> None:
    conn.execute("COMMIT")


def _abort(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def _selection_id(conn: sqlite3.Connection, user_id: int, slate_id: int, stamp: str) -> int:
    """The slate's selection row, created with the default limits on the first write."""
    row = conn.execute("SELECT id FROM selections WHERE slate_id = ?", (slate_id,)).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = conn.execute(
        "INSERT INTO selections (user_id, slate_id, lunch_limit, dinner_limit, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, slate_id, DEFAULT_LIMIT, DEFAULT_LIMIT, stamp),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _count_picks(conn: sqlite3.Connection, selection_id: int, meal_type: MealType) -> int:
    """Picks of one meal type, counting only options that are still active."""
    row = conn.execute(
        "SELECT COUNT(*) FROM selection_picks p JOIN slate_options o ON o.id = p.slate_option_id "
        "WHERE p.selection_id = ? AND o.meal_type = ? AND o.replaced_at IS NULL",
        (selection_id, meal_type.value),
    ).fetchone()
    return int(row[0])


def pick_option(
    conn: sqlite3.Connection, catalog: Catalog, user_id: int, option_id: int, *, now: datetime
) -> None:
    """Pick an available, active option of the user's slate, within the limit."""
    stamp = _timestamp(now)
    ref = get_active_option(conn, user_id, option_id)
    if ref is None:
        raise OptionNotFoundError(f"option {option_id} not found")
    current = get_current_profile(conn, user_id)
    if current is None:
        raise ProfileRequiredError("save a profile before picking an option")
    stored = ref.option
    result = check_option(
        stored.draft,
        catalog=catalog,
        servings=stored.servings,
        max_cook_minutes=current.profile.max_cook_minutes,
        exclusions=current.profile.exclusions,
    )
    if not isinstance(result, MealOption):
        raise OptionUnavailableError(result.reason)
    _begin(conn)
    try:
        # Re-read inside the transaction: the option may have been replaced in the meantime.
        if get_active_option(conn, user_id, option_id) is None:
            raise OptionNotFoundError(f"option {option_id} not found")
        _refuse_if_confirmed(conn, user_id, ref.slate_id)
        selection_id = _selection_id(conn, user_id, ref.slate_id, stamp)
        already = conn.execute(
            "SELECT 1 FROM selection_picks WHERE selection_id = ? AND slate_option_id = ?",
            (selection_id, option_id),
        ).fetchone()
        if already is None:
            lunch_limit, dinner_limit = _limits(conn, ref.slate_id)
            limit = lunch_limit if stored.meal_type is MealType.lunch else dinner_limit
            if _count_picks(conn, selection_id, stored.meal_type) >= limit:
                plural = _PLURAL[stored.meal_type]
                raise SelectionLimitError(f"You have picked {limit} of {limit} {plural}.")
            conn.execute(
                "INSERT INTO selection_picks (selection_id, user_id, slate_option_id, picked_at) "
                "VALUES (?, ?, ?, ?)",
                (selection_id, user_id, option_id, stamp),
            )
            conn.execute("UPDATE selections SET updated_at = ? WHERE id = ?", (stamp, selection_id))
        _finish(conn)
    except BaseException:
        _abort(conn)
        raise


def unpick_option(conn: sqlite3.Connection, user_id: int, option_id: int, *, now: datetime) -> None:
    """Remove a pick. Works on options that no longer fit the profile; a no-op if not picked."""
    stamp = _timestamp(now)
    ref = get_active_option(conn, user_id, option_id)
    if ref is None:
        raise OptionNotFoundError(f"option {option_id} not found")
    _begin(conn)
    try:
        _refuse_if_confirmed(conn, user_id, ref.slate_id)
        row = conn.execute(
            "SELECT id FROM selections WHERE slate_id = ? AND user_id = ?",
            (ref.slate_id, user_id),
        ).fetchone()
        if row is not None:
            deleted = conn.execute(
                "DELETE FROM selection_picks WHERE selection_id = ? AND slate_option_id = ? "
                "AND user_id = ?",
                (row["id"], option_id, user_id),
            )
            if deleted.rowcount:
                conn.execute(
                    "UPDATE selections SET updated_at = ? WHERE id = ?", (stamp, row["id"])
                )
        _finish(conn)
    except BaseException:
        _abort(conn)
        raise


def _valid_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_LIMIT <= value <= MAX_LIMIT:
        raise SelectionLimitError(
            f"A limit must be a whole number from {MIN_LIMIT} to {MAX_LIMIT}."
        )
    return value


def set_pick_limits(
    conn: sqlite3.Connection,
    user_id: int,
    week_start: date,
    lunch_limit: int,
    dinner_limit: int,
    *,
    now: datetime,
) -> None:
    """Set the week's limits. A limit below the current number of picks is refused."""
    limits = {
        MealType.lunch: _valid_limit(lunch_limit),
        MealType.dinner: _valid_limit(dinner_limit),
    }
    stamp = _timestamp(now)
    slate = get_slate(conn, user_id, week_start)
    if slate is None:
        raise SlateNotFoundError(f"no slate for the week of {week_start}")
    _begin(conn)
    try:
        _refuse_if_confirmed(conn, user_id, slate.id)
        selection_id = _selection_id(conn, user_id, slate.id, stamp)
        for meal_type, limit in limits.items():
            picked = _count_picks(conn, selection_id, meal_type)
            if limit < picked:
                raise SelectionLimitError(
                    f"You have picked {picked} {_PLURAL[meal_type]}. "
                    f"Unpick some before lowering the limit to {limit}."
                )
        conn.execute(
            "UPDATE selections SET lunch_limit = ?, dinner_limit = ?, updated_at = ? WHERE id = ?",
            (limits[MealType.lunch], limits[MealType.dinner], stamp, selection_id),
        )
        _finish(conn)
    except BaseException:
        _abort(conn)
        raise


def confirm_selection(
    conn: sqlite3.Connection, catalog: Catalog, user_id: int, week_start: date, *, now: datetime
) -> ConfirmedPlan:
    """Confirm the week's picks, including none, and store what was shown, picked and skipped.

    Makes no model call and generates nothing. Calories and macros are never checked: the
    user may confirm any selection. Refused while a picked option no longer fits the profile.
    """
    stamp = _timestamp(now)
    if get_slate(conn, user_id, week_start) is None:
        raise SlateNotFoundError(f"no slate for the week of {week_start}")
    _begin(conn)
    try:
        slate = load_slate_view(conn, catalog, user_id, week_start)
        assert slate is not None
        _refuse_if_confirmed(conn, user_id, slate.slate_id)
        picked = _picked_ids(conn, user_id, slate.slate_id)
        slots = [slot for mt in MealType for slot in slate.options[mt]]
        if any(isinstance(s, UnavailableOption) and s.option_id in picked for s in slots):
            raise UnavailablePickError(
                "A picked meal no longer fits your profile. Unpick it to confirm."
            )
        # Shown: the active options that pass the checks on this read.
        shown = [s.option_id for s in slots if isinstance(s, MealOption)]
        selection_id = _selection_id(conn, user_id, slate.slate_id, stamp)
        cursor = conn.execute(
            "INSERT INTO selection_confirmations (selection_id, user_id, confirmed_at) "
            "VALUES (?, ?, ?)",
            (selection_id, user_id, stamp),
        )
        confirmation_id = cursor.lastrowid
        assert confirmation_id is not None
        conn.executemany(
            "INSERT INTO confirmation_options (confirmation_id, user_id, slate_option_id, outcome) "
            "VALUES (?, ?, ?, ?)",
            [
                (confirmation_id, user_id, i, "picked" if i in picked else "skipped")
                for i in shown
                if i is not None
            ],
        )
        _finish(conn)
    except BaseException:
        _abort(conn)
        raise
    result = get_confirmed_plan(conn, catalog, user_id, week_start)
    assert result is not None
    return result


def reopen_selection(
    conn: sqlite3.Connection, user_id: int, week_start: date, *, now: datetime
) -> None:
    """Unlock the week by marking its current confirmation reopened. Snapshots are kept."""
    stamp = _timestamp(now)
    slate = get_slate(conn, user_id, week_start)
    if slate is None:
        raise SelectionNotConfirmedError("This week is not confirmed.")
    _begin(conn)
    try:
        confirmation_id = _current_confirmation_id(conn, user_id, slate.id)
        if confirmation_id is None:
            raise SelectionNotConfirmedError("This week is not confirmed.")
        conn.execute(
            "UPDATE selection_confirmations SET reopened_at = ? WHERE id = ?",
            (stamp, confirmation_id),
        )
        _finish(conn)
    except BaseException:
        _abort(conn)
        raise


def get_confirmed_plan(
    conn: sqlite3.Connection, catalog: Catalog, user_id: int, week_start: date
) -> ConfirmedPlan | None:
    """The confirmed plan, or None with no slate, no confirmation, or a reopened one.

    Downstream features (grocery list, recipes; ADRs 0021, 0024, 0025, 0027) must:
    - Build only from this function. Build nothing when it returns None, when ``meals`` is
      empty (a week with no meals planned) or when ``unavailable`` is non-empty.
    - Derive what is shown on every read; store no copy keyed to the plan.
    - Re-check any stored per-option artifact on read, so a meal that now fails the profile
      checks is never shown.

    ``meals`` are rebuilt through ``check_option`` against the current profile. Each has
    ``option_id`` set to its ``slate_options.id`` and the stored ``servings``, in plan order:
    lunches, then dinners, each by slate position. Picked meals that now fail the checks are
    listed in ``unavailable`` instead.
    """
    slate = get_slate(conn, user_id, week_start)
    if slate is None:
        return None
    confirmation_id = _current_confirmation_id(conn, user_id, slate.id)
    if confirmation_id is None:
        return None
    current = get_current_profile(conn, user_id)
    if current is None:
        raise ProfileRequiredError("a plan exists but the user has no saved profile")
    profile = current.profile
    rows = conn.execute(
        "SELECT o.id, o.servings, o.data FROM confirmation_options c "
        "JOIN slate_options o ON o.id = c.slate_option_id "
        "WHERE c.confirmation_id = ? AND c.user_id = ? AND c.outcome = 'picked' "
        "AND o.user_id = ? "
        "ORDER BY CASE o.meal_type WHEN 'lunch' THEN 0 ELSE 1 END, o.position, o.id",
        (confirmation_id, user_id, user_id),
    ).fetchall()
    meals: list[MealOption] = []
    unavailable: list[UnavailableOption] = []
    for row in rows:
        result = check_option(
            MealOptionDraft.model_validate_json(row["data"]),
            catalog=catalog,
            servings=row["servings"],
            max_cook_minutes=profile.max_cook_minutes,
            exclusions=profile.exclusions,
        )
        if isinstance(result, MealOption):
            meals.append(replace(result, option_id=int(row["id"])))
        else:
            unavailable.append(UnavailableOption(option_id=int(row["id"]), reason=result.reason))
    return ConfirmedPlan(
        confirmation_id=confirmation_id,
        week_start=week_start,
        meals=meals,
        unavailable=unavailable,
    )
