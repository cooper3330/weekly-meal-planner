"""Slate storage (ADR 0019). Plain functions; the clock is injected, and nothing is cached.

Only the draft JSON is stored. Nutrition is rebuilt on every read (see ``view.py``).
"""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from mealplanner.planning.errors import SlateExistsError
from mealplanner.planning.models import MealOption, MealType, RecentMeal
from mealplanner.planning.schema import MealOptionDraft


@dataclass(frozen=True)
class StoredOption:
    id: int
    meal_type: MealType
    position: int
    servings: int
    draft: MealOptionDraft


@dataclass(frozen=True)
class StoredSlate:
    id: int
    user_id: int
    week_start: date
    created_at: str
    profile_version: int
    options: dict[MealType, list[StoredOption]]


def _timestamp(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_slate(
    conn: sqlite3.Connection,
    user_id: int,
    week_start: date,
    profile_version: int,
    options: Mapping[MealType, Sequence[MealOption]],
    *,
    now: datetime,
) -> int:
    """Write the slate and all its options in one transaction; return the slate id."""
    created_at = _timestamp(now)
    week = week_start.isoformat()
    # Serialize first, so a bad option fails before any write.
    rows = [
        (MealType(meal_type).value, position, option.servings, option.draft.model_dump_json())
        for meal_type, group in options.items()
        for position, option in enumerate(group, start=1)
    ]
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT 1 FROM slates WHERE user_id = ? AND week_start = ?", (user_id, week)
        ).fetchone()
        if existing is not None:
            raise SlateExistsError(f"a slate already exists for the week of {week}")
        cursor = conn.execute(
            "INSERT INTO slates (user_id, week_start, created_at, profile_version) "
            "VALUES (?, ?, ?, ?)",
            (user_id, week, created_at, profile_version),
        )
        slate_id = cursor.lastrowid
        assert slate_id is not None
        conn.executemany(
            "INSERT INTO slate_options "
            "(slate_id, user_id, meal_type, position, servings, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (slate_id, user_id, mt, pos, servings, data, created_at)
                for mt, pos, servings, data in rows
            ],
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return slate_id


def get_slate(conn: sqlite3.Connection, user_id: int, week_start: date) -> StoredSlate | None:
    """The active options of the user's slate for the week, in position order."""
    slate = conn.execute(
        "SELECT id, user_id, week_start, created_at, profile_version FROM slates "
        "WHERE user_id = ? AND week_start = ?",
        (user_id, week_start.isoformat()),
    ).fetchone()
    if slate is None:
        return None
    grouped: dict[MealType, list[StoredOption]] = {mt: [] for mt in MealType}
    rows = conn.execute(
        "SELECT id, meal_type, position, servings, data FROM slate_options "
        "WHERE slate_id = ? AND user_id = ? AND replaced_at IS NULL "
        "ORDER BY meal_type, position",
        (slate["id"], user_id),
    ).fetchall()
    for row in rows:
        meal_type = MealType(row["meal_type"])
        grouped[meal_type].append(
            StoredOption(
                id=row["id"],
                meal_type=meal_type,
                position=row["position"],
                servings=row["servings"],
                draft=MealOptionDraft.model_validate_json(row["data"]),
            )
        )
    return StoredSlate(
        id=slate["id"],
        user_id=slate["user_id"],
        week_start=date.fromisoformat(slate["week_start"]),
        created_at=slate["created_at"],
        profile_version=slate["profile_version"],
        options=grouped,
    )


def recent_meals(
    conn: sqlite3.Connection, user_id: int, week_start: date, weeks: int
) -> list[RecentMeal]:
    """Every option, active or replaced, in slates of the ``weeks`` weeks before ``week_start``."""
    if weeks <= 0:
        return []
    earliest = week_start - timedelta(days=7 * weeks)
    rows = conn.execute(
        "SELECT o.data FROM slate_options o JOIN slates s ON s.id = o.slate_id "
        "WHERE s.user_id = ? AND o.user_id = ? AND s.week_start >= ? AND s.week_start < ? "
        "ORDER BY s.week_start, o.id",
        (user_id, user_id, earliest.isoformat(), week_start.isoformat()),
    ).fetchall()
    meals = []
    for row in rows:
        draft = MealOptionDraft.model_validate_json(row["data"])
        meals.append(
            RecentMeal(
                name=draft.name,
                ingredient_ids=frozenset(line.ingredient_id for line in draft.ingredients),
            )
        )
    return meals
