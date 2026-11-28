import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from mealplanner.db.connection import connect
from mealplanner.recipes.errors import RecipeNotFoundError
from mealplanner.recipes.schema import RecipeDraft
from mealplanner.recipes.store import get_recipe_steps, save_recipe
from recipes.recipe_support import (
    GEN_NOW,
    LATER,
    add_user,
    confirmed_week,
    make_meal,
    valid_payload,
)

MEAL = make_meal()


def draft(prep: int = 10) -> RecipeDraft:
    return RecipeDraft.model_validate(valid_payload(MEAL, prep=prep))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c, _ = confirmed_week(tmp_path)
    return c


def first_option(conn: sqlite3.Connection, user_id: int = 1) -> int:
    row = conn.execute("SELECT MIN(id) FROM slate_options WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[0])


def test_nothing_stored_gives_none(conn: sqlite3.Connection) -> None:
    assert get_recipe_steps(conn, 1, first_option(conn)) is None


def test_save_then_get_round_trips(conn: sqlite3.Connection) -> None:
    option = first_option(conn)
    save_recipe(conn, 1, option, draft(), now=GEN_NOW)
    assert get_recipe_steps(conn, 1, option) == draft()
    row = conn.execute("SELECT user_id, generated_at FROM recipes").fetchone()
    assert (row["user_id"], row["generated_at"]) == (1, "2026-11-01T12:10:00Z")


def test_save_replaces_the_one_row(conn: sqlite3.Connection) -> None:
    option = first_option(conn)
    save_recipe(conn, 1, option, draft(10), now=GEN_NOW)
    save_recipe(conn, 1, option, draft(5), now=GEN_NOW + timedelta(minutes=1))
    rows = conn.execute("SELECT generated_at FROM recipes").fetchall()
    assert [r["generated_at"] for r in rows] == ["2026-11-01T12:11:00Z"]
    stored = get_recipe_steps(conn, 1, option)
    assert stored is not None
    assert stored.prep_minutes == 5


def test_reads_only_own_row(conn: sqlite3.Connection) -> None:
    add_user(conn, 2)
    option = first_option(conn)
    save_recipe(conn, 1, option, draft(), now=GEN_NOW)
    assert get_recipe_steps(conn, 2, option) is None


def test_cannot_save_for_another_users_option(conn: sqlite3.Connection) -> None:
    add_user(conn, 2)
    with pytest.raises(RecipeNotFoundError):
        save_recipe(conn, 2, first_option(conn), draft(), now=GEN_NOW)
    assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0


def test_cannot_overwrite_another_users_row(conn: sqlite3.Connection) -> None:
    add_user(conn, 2)
    option = first_option(conn)
    save_recipe(conn, 1, option, draft(10), now=GEN_NOW)
    with pytest.raises(RecipeNotFoundError):
        save_recipe(conn, 2, option, draft(5), now=LATER)
    stored = get_recipe_steps(conn, 1, option)
    assert stored is not None
    assert stored.prep_minutes == 10


def test_no_day_or_nutrition_columns(conn: sqlite3.Connection) -> None:
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(recipes)")]
    assert columns == ["id", "user_id", "slate_option_id", "data", "generated_at"]
    for word in ("day", "kcal", "protein", "carb", "fat", "quantity"):
        assert not any(word in c for c in columns)


def test_survives_reconnect(conn: sqlite3.Connection, tmp_path: Path) -> None:
    option = first_option(conn)
    save_recipe(conn, 1, option, draft(), now=GEN_NOW)
    conn.close()
    again = connect(tmp_path / "app.sqlite3")
    try:
        assert get_recipe_steps(again, 1, option) == draft()
    finally:
        again.close()
