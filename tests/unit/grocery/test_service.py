import inspect
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from grocery_support import CAT, INFO

from mealplanner.db.connection import connect
from mealplanner.db.migrate import apply_migrations
from mealplanner.grocery import service
from mealplanner.grocery.build import build_grocery_list
from mealplanner.grocery.service import (
    MealNoLongerFits,
    NoMealsPlanned,
    Ready,
    WeekOpen,
    load_grocery_page,
)
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.errors import ProfileRequiredError
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.selection import (
    confirm_selection,
    get_confirmed_plan,
    pick_option,
    reopen_selection,
    unpick_option,
)
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "src" / "mealplanner" / "db" / "migrations"
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
WEEK = date(2026, 11, 2)

Lines = Sequence[tuple[str, float, str]]
# Every meal holds 200 g of "dense" (500 kcal per 100 g), so the calorie check passes.
LUNCHES: list[tuple[str, Lines]] = [
    ("Chicken Rice", [("chicken", 100, "g"), ("rice", 100, "g")]),
    ("Egg Plate", [("egg", 2, "each")]),
    ("Oil Bowl", [("olive_oil", 1, "tbsp")]),
    ("Soy Bowl", [("soy_sauce", 1, "tbsp")]),
]
# Filler meals bring each type to the slate minimum; their ingredient sets all differ.
_FILLER: list[list[tuple[str, float, str]]] = [
    [],
    [("chicken", 50, "g")],
    [("rice", 50, "g")],
    [("egg", 1, "each")],
]
LUNCHES += [
    (f"Filler lunch {i}", [("chicken_stock", 100, "ml"), *x]) for i, x in enumerate(_FILLER)
]
_POOL = ("peanut_butter", "chicken_stock", "rice", "egg")
# Every dinner also holds olive oil, so none repeats a lunch's ingredient set.
DINNERS: list[tuple[str, Lines]] = [
    (
        f"Dinner {n}",
        [("olive_oil", 5, "g"), *((p, 50, "g") for b, p in enumerate(_POOL) if n >> b & 1)],
    )
    for n in range(1, 9)
]


def draft(name: str, lines: Lines) -> dict[str, Any]:
    ingredients = [{"ingredient_id": "dense", "quantity": 200, "unit": "g"}]
    ingredients += [{"ingredient_id": i, "quantity": q, "unit": u} for i, q, u in lines]
    return {
        "name": name,
        "description": "A meal for tests.",
        "cook_minutes": 30,
        "tags": ["quick"],
        "ingredients": ingredients,
    }


def make_slate(conn: sqlite3.Connection, user_id: int = 1) -> None:
    lunches = {"options": [draft(n, ls) for n, ls in LUNCHES]}
    dinners = {"options": [draft(n, ls) for n, ls in DINNERS]}
    generate_slate(
        conn,
        FakeLLMClient([lunches, dinners]),
        CAT,
        user_id,
        week_start=WEEK,
        now=NOW,
        lunch_count=len(LUNCHES),
        dinner_count=len(DINNERS),
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "app.sqlite3"
    apply_migrations(path, MIGRATIONS, tmp_path / "backups")
    c = connect(path)
    save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
    make_slate(c)
    yield c
    c.close()


def option_id(conn: sqlite3.Connection, name: str, user_id: int = 1) -> int:
    rows = conn.execute(
        "SELECT id, data FROM slate_options WHERE user_id = ? AND replaced_at IS NULL", (user_id,)
    ).fetchall()
    for r in rows:
        if f'"{name}"' in r["data"]:
            return int(r["id"])
    raise AssertionError(name)


def pick(conn: sqlite3.Connection, *names: str, user_id: int = 1) -> None:
    for n in names:
        pick_option(conn, CAT, user_id, option_id(conn, n, user_id), now=NOW)


def confirm(conn: sqlite3.Connection, user_id: int = 1) -> None:
    confirm_selection(conn, CAT, user_id, WEEK, now=LATER)


def load(conn: sqlite3.Connection, user_id: int = 1) -> Any:
    return load_grocery_page(conn, CAT, INFO, user_id, WEEK)


def ids(page: Ready) -> set[str]:
    return {i.ingredient_id for s in page.list.sections for i in s.items}


def test_open_week_no_list(conn: sqlite3.Connection) -> None:
    pick(conn, "Chicken Rice", "Egg Plate")
    assert isinstance(load(conn), WeekOpen)


def test_confirmed_week_list(conn: sqlite3.Connection) -> None:
    pick(conn, "Chicken Rice", "Egg Plate")
    confirm(conn)
    page = load(conn)
    assert isinstance(page, Ready)
    assert ids(page) == {"dense", "chicken", "rice", "egg"}


def test_reopened_week_no_list(conn: sqlite3.Connection) -> None:
    pick(conn, "Chicken Rice")
    confirm(conn)
    reopen_selection(conn, 1, WEEK, now=LATER)
    assert isinstance(load(conn), WeekOpen)


def test_reconfirm_regenerates(conn: sqlite3.Connection) -> None:
    pick(conn, "Chicken Rice", "Egg Plate")
    confirm(conn)
    reopen_selection(conn, 1, WEEK, now=LATER)
    unpick_option(conn, 1, option_id(conn, "Egg Plate"), now=LATER)
    pick(conn, "Oil Bowl")
    confirm_selection(conn, CAT, 1, WEEK, now=LATER + timedelta(minutes=1))
    page = load(conn)
    assert isinstance(page, Ready)
    assert "olive_oil" in ids(page)
    assert "egg" not in ids(page)


def test_unavailable_meal_withholds_list(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    pick(conn, "Soy Bowl", "Egg Plate")
    confirm(conn)
    calls: list[object] = []
    monkeypatch.setattr(service, "build_grocery_list", lambda *a, **k: calls.append(1))
    no_soy = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.soy})})
    save_profile(conn, 1, no_soy, now=LATER)
    assert isinstance(load(conn), MealNoLongerFits)
    assert calls == []


def test_empty_confirmation(conn: sqlite3.Connection) -> None:
    confirm(conn)
    assert isinstance(load(conn), NoMealsPlanned)


def test_servings_change_rescales(conn: sqlite3.Connection) -> None:
    pick(conn, "Chicken Rice")
    confirm(conn)
    before = load(conn)
    assert DEFAULT_PROFILE.servings == 2
    save_profile(conn, 1, DEFAULT_PROFILE.model_copy(update={"servings": 4}), now=LATER)
    after = load(conn)

    def amounts(page: Ready) -> dict[str, float]:
        return {i.ingredient_id: i.needs[0].amount for s in page.list.sections for i in s.items}

    b, a = amounts(before), amounts(after)
    assert b.keys() == a.keys()
    for k in b:
        assert a[k] == pytest.approx(2 * b[k])


def test_no_profile_raises(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"
    apply_migrations(path, MIGRATIONS, tmp_path / "backups")
    c = connect(path)
    with pytest.raises(ProfileRequiredError):
        load(c)
    c.close()


def test_no_model_call(conn: sqlite3.Connection) -> None:
    # The service takes no model client at all, so nothing can call one.
    assert set(inspect.signature(load_grocery_page).parameters) == {
        "conn",
        "catalog",
        "grocery_info",
        "user_id",
        "week_start",
    }
    pick(conn, "Chicken Rice")
    confirm(conn)
    assert isinstance(load(conn), Ready)


def snapshot(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    ]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608


def test_nothing_written(conn: sqlite3.Connection) -> None:
    pick(conn, "Chicken Rice")
    confirm(conn)
    before = snapshot(conn)
    assert isinstance(load(conn), Ready)
    assert snapshot(conn) == before


def test_second_user_isolated(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    save_profile(conn, 2, DEFAULT_PROFILE, now=NOW)
    make_slate(conn, 2)
    pick(conn, "Chicken Rice")
    confirm(conn)
    assert isinstance(load(conn, 1), Ready)
    assert isinstance(load(conn, 2), WeekOpen)


def test_ready_matches_builder(conn: sqlite3.Connection) -> None:
    pick(conn, "Chicken Rice", "Egg Plate")
    confirm(conn)
    plan = get_confirmed_plan(conn, CAT, 1, WEEK)
    assert plan is not None
    expected = build_grocery_list(
        plan.meals, catalog=CAT, grocery_info=INFO, servings=DEFAULT_PROFILE.servings
    )
    assert load(conn).list == expected
