"""Draft picks, pick limits and the nutrition summary of a week's slate (ADR 0021).

Plain functions with an explicit connection and user id (ADR 0011). Nothing here calls the
model, and nothing stores nutrition: every number comes from ``load_slate_view``.
"""

import sqlite3
from collections.abc import Sequence
from datetime import date, datetime

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.compute import Nutrition
from mealplanner.planning.checks import check_option
from mealplanner.planning.errors import (
    OptionNotFoundError,
    OptionUnavailableError,
    ProfileRequiredError,
    SelectionLimitError,
    SlateNotFoundError,
)
from mealplanner.planning.models import (
    MealOption,
    MealType,
    NutritionSummary,
    SelectionItem,
    SelectionView,
)
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
    return SelectionView(
        slate_id=slate.slate_id,
        week_start=slate.week_start,
        lunch_limit=lunch_limit,
        dinner_limit=dinner_limit,
        items=items,
        pick_counts={mt: sum(1 for i in items[mt] if i.picked) for mt in MealType},
        summary=summarize(chosen),
    )


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
