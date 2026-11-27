import sqlite3
from collections.abc import Iterator
from dataclasses import fields
from datetime import timedelta
from pathlib import Path

import pytest
from slate_support import CAT, MIGRATIONS, NOW, WEEK, migrated
from test_selection_picks import NO_SOY, add_second_user, ids, make_slate, view

from mealplanner.db.connection import connect
from mealplanner.db.migrate import apply_migrations
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.compute import compute_nutrition
from mealplanner.planning.errors import (
    OptionNotFoundError,
    SelectionLockedError,
    SelectionNotConfirmedError,
    SlateNotFoundError,
    UnavailablePickError,
)
from mealplanner.planning.models import ConfirmedPlan, MealOption
from mealplanner.planning.selection import (
    confirm_selection,
    get_confirmed_plan,
    load_selection_view,
    pick_option,
    reopen_selection,
    set_pick_limits,
    unpick_option,
)
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

LATER = NOW + timedelta(minutes=5)
LATEST = NOW + timedelta(minutes=10)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(migrated(tmp_path))
    save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
    make_slate(c)
    yield c
    c.close()


def pick(conn: sqlite3.Connection, *option_ids: int) -> None:
    for i in option_ids:
        pick_option(conn, CAT, 1, i, now=NOW)


def confirm(conn: sqlite3.Connection, user_id: int = 1) -> ConfirmedPlan:
    return confirm_selection(conn, CAT, user_id, WEEK, now=LATER)


def plan(conn: sqlite3.Connection) -> ConfirmedPlan:
    p = get_confirmed_plan(conn, CAT, 1, WEEK)
    assert p is not None
    return p


def outcomes(conn: sqlite3.Connection, confirmation_id: int) -> dict[int, str]:
    rows = conn.execute(
        "SELECT slate_option_id, outcome FROM confirmation_options WHERE confirmation_id = ?",
        (confirmation_id,),
    ).fetchall()
    return {r["slate_option_id"]: r["outcome"] for r in rows}


def test_no_plan_before_confirm(conn: sqlite3.Connection) -> None:
    pick(conn, ids(conn, "lunch")[0])
    assert get_confirmed_plan(conn, CAT, 1, WEEK) is None


def test_no_plan_without_slate(tmp_path: Path) -> None:
    c = connect(migrated(tmp_path))
    try:
        assert get_confirmed_plan(c, CAT, 1, WEEK) is None
        with pytest.raises(SlateNotFoundError):
            confirm_selection(c, CAT, 1, WEEK, now=NOW)
    finally:
        c.close()


def test_confirm_saves_plan(conn: sqlite3.Connection) -> None:
    lunch, dinner = ids(conn, "lunch"), ids(conn, "dinner")
    pick(conn, lunch[0], dinner[1])
    result = confirm(conn)
    assert result == plan(conn)
    assert len(result.meals) == 2
    for meal in result.meals:
        lines = [(i.ingredient_id, i.quantity, i.unit) for i in meal.draft.ingredients]
        assert meal.nutrition == compute_nutrition(lines, CAT, meal.servings)
    assert result.unavailable == []


def test_plan_meals_contract(conn: sqlite3.Connection) -> None:
    lunch, dinner = ids(conn, "lunch"), ids(conn, "dinner")
    # Different stored servings per option.
    for n, option_id in enumerate([lunch[0], lunch[2], dinner[1]], start=2):
        conn.execute("UPDATE slate_options SET servings = ? WHERE id = ?", (n, option_id))
    conn.commit()
    pick(conn, dinner[1], lunch[2], lunch[0])
    result = confirm(conn)
    assert [m.option_id for m in result.meals] == [lunch[0], lunch[2], dinner[1]]
    assert [m.servings for m in result.meals] == [2, 3, 4]
    for m in result.meals:
        stored = conn.execute(
            "SELECT servings FROM slate_options WHERE id = ?", (m.option_id,)
        ).fetchone()
        assert m.servings == stored["servings"]
    assert all(isinstance(m, MealOption) and m.option_id is not None for m in result.meals)


def test_plan_survives_restart(tmp_path: Path) -> None:
    path = migrated(tmp_path)
    c = connect(path)
    save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
    make_slate(c)
    pick(c, ids(c, "lunch")[0], ids(c, "dinner")[0])
    before = confirm(c)
    c.close()
    apply_migrations(path, MIGRATIONS, tmp_path / "backups")
    c2 = connect(path)
    try:
        assert get_confirmed_plan(c2, CAT, 1, WEEK) == before
    finally:
        c2.close()


def test_shown_picked_skipped_stored(conn: sqlite3.Connection) -> None:
    lunch, dinner = ids(conn, "lunch"), ids(conn, "dinner")
    picked = lunch[:3] + dinner[:2]
    pick(conn, *picked)
    result = confirm(conn)
    stored = outcomes(conn, result.confirmation_id)
    assert len(stored) == 16
    assert {i for i, o in stored.items() if o == "picked"} == set(picked)
    assert {i for i, o in stored.items() if o == "skipped"} == set(lunch + dinner) - set(picked)


def test_shown_excludes_unavailable_and_replaced(tmp_path: Path) -> None:
    c = connect(migrated(tmp_path))
    try:
        save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
        make_slate(c, soy_index=2)
        lunch = ids(c, "lunch")
        # Replace lunch 0 by hand: retire it and add a new active row in its slot.
        old = lunch[0]
        c.execute("UPDATE slate_options SET replaced_at = ? WHERE id = ?", ("2026-11-01", old))
        c.commit()
        save_profile(c, 1, NO_SOY, now=LATER)
        result = confirm(c)
        stored = outcomes(c, result.confirmation_id)
        assert lunch[2] not in stored
        assert old not in stored
        assert len(stored) == 14
    finally:
        c.close()


@pytest.mark.parametrize("scenario", ["high", "low", "one"])
def test_confirm_any_selection(conn: sqlite3.Connection, scenario: str) -> None:
    lunch, dinner = ids(conn, "lunch"), ids(conn, "dinner")
    if scenario == "high":
        pick(conn, *lunch[:5], *dinner[:5])
    elif scenario == "low":
        pick(conn, lunch[0])
    else:
        set_pick_limits(conn, 1, WEEK, 5, 5, now=NOW)
        pick(conn, dinner[0])
    assert confirm(conn).confirmation_id


@pytest.mark.parametrize("limits", [(5, 5), (0, 0)])
def test_confirm_empty_allowed(conn: sqlite3.Connection, limits: tuple[int, int]) -> None:
    set_pick_limits(conn, 1, WEEK, *limits, now=NOW)
    result = confirm(conn)
    assert result.meals == []
    assert result.unavailable == []
    assert set(outcomes(conn, result.confirmation_id).values()) == {"skipped"}
    assert len(outcomes(conn, result.confirmation_id)) == 16
    again = get_confirmed_plan(conn, CAT, 1, WEEK)
    assert again is not None
    assert again.meals == []
    assert again.unavailable == []


def test_confirm_after_unpicking_unavailable(tmp_path: Path) -> None:
    c = connect(migrated(tmp_path))
    try:
        save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
        make_slate(c, soy_index=0)
        soy = ids(c, "lunch")[0]
        pick(c, soy)
        save_profile(c, 1, NO_SOY, now=LATER)
        with pytest.raises(UnavailablePickError):
            confirm(c)
        unpick_option(c, 1, soy, now=LATER)
        result = confirm(c)
        assert result.meals == []
    finally:
        c.close()


def test_confirm_with_unavailable_pick_refused(tmp_path: Path) -> None:
    c = connect(migrated(tmp_path))
    try:
        save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
        make_slate(c, soy_index=0)
        pick(c, ids(c, "lunch")[0], ids(c, "lunch")[1])
        save_profile(c, 1, NO_SOY, now=LATER)
        with pytest.raises(UnavailablePickError):
            confirm(c)
        assert c.execute("SELECT COUNT(*) FROM selection_confirmations").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM confirmation_options").fetchone()[0] == 0
        assert get_confirmed_plan(c, CAT, 1, WEEK) is None
    finally:
        c.close()


def test_confirm_makes_no_model_call(conn: sqlite3.Connection) -> None:
    fake = FakeLLMClient([])  # fails on any call
    pick(conn, ids(conn, "lunch")[0])
    confirm(conn)
    assert fake is not None


def test_locked_while_confirmed(conn: sqlite3.Connection) -> None:
    lunch = ids(conn, "lunch")
    pick(conn, lunch[0])
    confirm(conn)
    with pytest.raises(SelectionLockedError):
        pick_option(conn, CAT, 1, lunch[1], now=LATER)
    with pytest.raises(SelectionLockedError):
        unpick_option(conn, 1, lunch[0], now=LATER)
    with pytest.raises(SelectionLockedError):
        set_pick_limits(conn, 1, WEEK, 6, 6, now=LATER)
    with pytest.raises(SelectionLockedError):
        confirm(conn)
    assert conn.execute("SELECT COUNT(*) FROM selection_confirmations").fetchone()[0] == 1


def test_reopen_hides_plan(conn: sqlite3.Connection) -> None:
    pick(conn, ids(conn, "lunch")[0])
    first = confirm(conn)
    snapshot = outcomes(conn, first.confirmation_id)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    assert get_confirmed_plan(conn, CAT, 1, WEEK) is None
    assert outcomes(conn, first.confirmation_id) == snapshot
    row = conn.execute("SELECT reopened_at FROM selection_confirmations").fetchone()
    assert row["reopened_at"] is not None
    assert not view(conn).confirmed


def test_reconfirm_changed(conn: sqlite3.Connection) -> None:
    lunch = ids(conn, "lunch")
    pick(conn, lunch[0], lunch[1])
    first = confirm(conn)
    snapshot = outcomes(conn, first.confirmation_id)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    unpick_option(conn, 1, lunch[1], now=LATEST)
    pick_option(conn, CAT, 1, lunch[2], now=LATEST)
    second = confirm_selection(conn, CAT, 1, WEEK, now=LATEST)
    assert second.confirmation_id != first.confirmation_id
    got = plan(conn)
    assert got.confirmation_id == second.confirmation_id
    assert [m.option_id for m in got.meals] == [lunch[0], lunch[2]]
    assert outcomes(conn, first.confirmation_id) == snapshot


def test_reconfirm_unchanged(conn: sqlite3.Connection) -> None:
    lunch = ids(conn, "lunch")
    pick(conn, lunch[0], lunch[1])
    first = confirm(conn)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    second = confirm_selection(conn, CAT, 1, WEEK, now=LATEST)
    assert second.confirmation_id != first.confirmation_id
    assert second.meals == first.meals
    a, b = outcomes(conn, first.confirmation_id), outcomes(conn, second.confirmation_id)
    assert a == b
    assert {i for i, o in a.items() if o == "picked"} == {lunch[0], lunch[1]}


def test_reopen_when_open_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(SelectionNotConfirmedError):
        reopen_selection(conn, 1, WEEK, now=LATER)
    confirm(conn)
    reopen_selection(conn, 1, WEEK, now=LATER)
    with pytest.raises(SelectionNotConfirmedError):
        reopen_selection(conn, 1, WEEK, now=LATER)


def test_confirmed_meal_becomes_unavailable(tmp_path: Path) -> None:
    c = connect(migrated(tmp_path))
    try:
        save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
        make_slate(c, soy_index=0)
        lunch = ids(c, "lunch")
        pick(c, lunch[0], lunch[1])
        first = confirm(c)
        save_profile(c, 1, NO_SOY, now=LATEST)
        got = plan(c)
        assert got.confirmation_id == first.confirmation_id
        assert [m.option_id for m in got.meals] == [lunch[1]]
        assert [u.option_id for u in got.unavailable] == [lunch[0]]
        assert outcomes(c, first.confirmation_id)[lunch[0]] == "picked"
        v = view(c)
        assert v.confirmed
        assert v.confirmation_id == first.confirmation_id
        assert [u.option_id for u in v.confirmed_unavailable] == [lunch[0]]
    finally:
        c.close()


def test_view_confirmed_fields_when_open(conn: sqlite3.Connection) -> None:
    v = view(conn)
    assert v.confirmed is False
    assert v.confirmation_id is None
    assert v.confirmed_unavailable == ()


def test_second_user_isolated(conn: sqlite3.Connection) -> None:
    add_second_user(conn)
    pick(conn, ids(conn, "lunch")[0])
    first = confirm(conn)
    assert get_confirmed_plan(conn, CAT, 2, WEEK) is None
    with pytest.raises(SelectionNotConfirmedError):
        reopen_selection(conn, 2, WEEK, now=LATER)
    second = confirm(conn, 2)
    assert second.meals == []
    assert plan(conn) == first
    assert load_selection_view(conn, CAT, 2, WEEK) is not None
    with pytest.raises(OptionNotFoundError):
        pick_option(conn, CAT, 2, ids(conn, "lunch")[1], now=LATER)


def test_no_day_field(conn: sqlite3.Connection) -> None:
    for table in ("selection_confirmations", "confirmation_options"):
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert cols
        assert not [c for c in cols if "day" in c.lower()]
    assert not [f.name for f in fields(ConfirmedPlan) if "day" in f.name.lower()]
    assert not [f.name for f in fields(MealOption) if "day" in f.name.lower()]
