import sqlite3
from collections.abc import Iterator
from dataclasses import fields
from datetime import timedelta
from pathlib import Path

import pytest
from slate_support import CAT, DINNER_START, NOW, WEEK, batch, migrated, option, plain_batch

from mealplanner.db.connection import connect
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.errors import (
    OptionNotFoundError,
    OptionUnavailableError,
    SelectionLimitError,
    SlateNotFoundError,
)
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.models import (
    MealOption,
    MealType,
    SelectionItem,
    SelectionView,
    UnavailableOption,
)
from mealplanner.planning.selection import (
    load_selection_view,
    pick_option,
    set_pick_limits,
    summarize,
    unpick_option,
)
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile

LATER = NOW + timedelta(minutes=5)
NO_SOY = DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.soy})})


def make_slate(conn: sqlite3.Connection, user_id: int = 1, soy_index: int | None = None) -> None:
    lunches = [option(i) for i in range(8)]
    if soy_index is not None:
        lunches[soy_index] = option(40, name="Soy Bowl", extra=[("soy_sauce", 1, "tbsp")])
    generate_slate(
        conn,
        FakeLLMClient([batch(*lunches), plain_batch(DINNER_START + user_id * 8, 8)]),
        CAT,
        user_id,
        week_start=WEEK,
        now=NOW,
        lunch_count=8,
        dinner_count=8,
    )


def add_second_user(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    save_profile(conn, 2, DEFAULT_PROFILE, now=NOW)
    make_slate(conn, 2)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(migrated(tmp_path))
    save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
    yield c
    c.close()


def ids(conn: sqlite3.Connection, meal_type: str, user_id: int = 1) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM slate_options WHERE user_id = ? AND meal_type = ? "
        "AND replaced_at IS NULL ORDER BY position",
        (user_id, meal_type),
    ).fetchall()
    return [r["id"] for r in rows]


def view(conn: sqlite3.Connection, user_id: int = 1) -> SelectionView:
    v = load_selection_view(conn, CAT, user_id, WEEK)
    assert v is not None
    return v


def picked_ids(v: SelectionView) -> list[int]:
    return [i.slot.option_id for mt in MealType for i in v.items[mt] if i.picked]


def test_no_slate_gives_none(conn: sqlite3.Connection) -> None:
    assert load_selection_view(conn, CAT, 1, WEEK) is None


def test_defaults_without_row(conn: sqlite3.Connection) -> None:
    make_slate(conn)
    v = view(conn)
    assert (v.lunch_limit, v.dinner_limit) == (5, 5)
    assert v.pick_counts == {MealType.lunch: 0, MealType.dinner: 0}
    assert picked_ids(v) == []
    assert v.summary.count == 0
    assert conn.execute("SELECT COUNT(*) FROM selections").fetchone()[0] == 0


def test_pick_and_unpick(conn: sqlite3.Connection) -> None:
    make_slate(conn)
    lunch = ids(conn, "lunch")
    dinner = ids(conn, "dinner")
    pick_option(conn, CAT, 1, lunch[0], now=NOW)
    pick_option(conn, CAT, 1, lunch[1], now=NOW)
    pick_option(conn, CAT, 1, dinner[0], now=NOW)
    pick_option(conn, CAT, 1, lunch[0], now=NOW)  # no-op
    v = view(conn)
    assert sorted(picked_ids(v)) == sorted([lunch[0], lunch[1], dinner[0]])
    chosen = [
        i.slot
        for mt in MealType
        for i in v.items[mt]
        if i.picked and isinstance(i.slot, MealOption)
    ]
    assert v.summary == summarize(chosen)
    assert v.summary.count == 3
    assert v.pick_counts == {MealType.lunch: 2, MealType.dinner: 1}
    unpick_option(conn, 1, lunch[0], now=LATER)
    unpick_option(conn, 1, lunch[0], now=LATER)  # no-op
    unpick_option(conn, 1, lunch[5], now=LATER)  # never picked: no-op
    v = view(conn)
    assert sorted(picked_ids(v)) == sorted([lunch[1], dinner[0]])
    assert v.summary.count == 2


def test_limit_is_a_cap(conn: sqlite3.Connection) -> None:
    make_slate(conn)
    lunch = ids(conn, "lunch")
    for i in lunch[:5]:
        pick_option(conn, CAT, 1, i, now=NOW)
    with pytest.raises(SelectionLimitError):
        pick_option(conn, CAT, 1, lunch[5], now=NOW)
    pick_option(conn, CAT, 1, lunch[0], now=NOW)  # already picked: still a no-op
    pick_option(conn, CAT, 1, ids(conn, "dinner")[0], now=NOW)
    assert view(conn).pick_counts == {MealType.lunch: 5, MealType.dinner: 1}


@pytest.mark.parametrize("bad", [-1, 15, True, 2.5, "3", None])
def test_limits_bounds(conn: sqlite3.Connection, bad: object) -> None:
    make_slate(conn)
    with pytest.raises(SelectionLimitError):
        set_pick_limits(conn, 1, WEEK, bad, 5, now=NOW)  # type: ignore[arg-type]
    with pytest.raises(SelectionLimitError):
        set_pick_limits(conn, 1, WEEK, 5, bad, now=NOW)  # type: ignore[arg-type]
    assert conn.execute("SELECT COUNT(*) FROM selections").fetchone()[0] == 0


def test_limit_zero_blocks_picks(conn: sqlite3.Connection) -> None:
    make_slate(conn)
    set_pick_limits(conn, 1, WEEK, 0, 14, now=NOW)
    v = view(conn)
    assert (v.lunch_limit, v.dinner_limit) == (0, 14)
    with pytest.raises(SelectionLimitError):
        pick_option(conn, CAT, 1, ids(conn, "lunch")[0], now=NOW)
    pick_option(conn, CAT, 1, ids(conn, "dinner")[0], now=NOW)


def test_lower_limit_below_picks_refused(conn: sqlite3.Connection) -> None:
    make_slate(conn)
    for i in ids(conn, "lunch")[:3]:
        pick_option(conn, CAT, 1, i, now=NOW)
    with pytest.raises(SelectionLimitError):
        set_pick_limits(conn, 1, WEEK, 2, 5, now=NOW)
    v = view(conn)
    assert v.lunch_limit == 5
    assert v.pick_counts[MealType.lunch] == 3
    set_pick_limits(conn, 1, WEEK, 3, 5, now=NOW)
    assert view(conn).lunch_limit == 3


def test_set_limits_without_slate(conn: sqlite3.Connection) -> None:
    with pytest.raises(SlateNotFoundError):
        set_pick_limits(conn, 1, WEEK, 3, 3, now=NOW)


def test_pick_refuses_replaced_foreign_unknown(conn: sqlite3.Connection) -> None:
    make_slate(conn)
    add_second_user(conn)
    target = ids(conn, "lunch")[0]
    conn.execute("UPDATE slate_options SET replaced_at = ? WHERE id = ?", ("2026-11-01", target))
    foreign = ids(conn, "lunch", 2)[0]
    for bad in (target, foreign, 999):
        with pytest.raises(OptionNotFoundError):
            pick_option(conn, CAT, 1, bad, now=NOW)
        with pytest.raises(OptionNotFoundError):
            unpick_option(conn, 1, bad, now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM selection_picks").fetchone()[0] == 0


def test_unavailable_cannot_be_picked(conn: sqlite3.Connection) -> None:
    make_slate(conn, soy_index=2)
    save_profile(conn, 1, NO_SOY, now=LATER)
    with pytest.raises(OptionUnavailableError):
        pick_option(conn, CAT, 1, ids(conn, "lunch")[2], now=LATER)
    assert picked_ids(view(conn)) == []


def test_picked_then_unavailable(conn: sqlite3.Connection) -> None:
    make_slate(conn, soy_index=2)
    lunch = ids(conn, "lunch")
    pick_option(conn, CAT, 1, lunch[0], now=NOW)
    pick_option(conn, CAT, 1, lunch[2], now=NOW)
    save_profile(conn, 1, NO_SOY, now=LATER)
    v = view(conn)
    flagged = [i.slot for i in v.items[MealType.lunch] if i.picked]
    assert isinstance(flagged[0], MealOption)
    assert isinstance(flagged[1], UnavailableOption)
    assert v.pick_counts[MealType.lunch] == 2
    assert v.summary.count == 1
    assert v.summary == summarize([flagged[0]])
    unpick_option(conn, 1, lunch[2], now=LATER)
    assert picked_ids(view(conn)) == [lunch[0]]


def test_picks_survive_reconnect(tmp_path: Path) -> None:
    path = migrated(tmp_path)
    first = connect(path)
    save_profile(first, 1, DEFAULT_PROFILE, now=NOW)
    make_slate(first)
    lunch = ids(first, "lunch")
    pick_option(first, CAT, 1, lunch[0], now=NOW)
    set_pick_limits(first, 1, WEEK, 4, 6, now=NOW)
    first.close()
    second = connect(path)
    try:
        v = view(second)
        assert picked_ids(v) == [lunch[0]]
        assert (v.lunch_limit, v.dinner_limit) == (4, 6)
    finally:
        second.close()


def test_second_user_isolated(conn: sqlite3.Connection) -> None:
    make_slate(conn)
    add_second_user(conn)
    pick_option(conn, CAT, 2, ids(conn, "lunch", 2)[0], now=NOW)
    set_pick_limits(conn, 2, WEEK, 1, 1, now=NOW)
    one = view(conn, 1)
    assert picked_ids(one) == []
    assert (one.lunch_limit, one.dinner_limit) == (5, 5)
    two = view(conn, 2)
    assert (two.lunch_limit, two.dinner_limit) == (1, 1)
    assert two.summary.count == 1
    assert conn.execute("SELECT DISTINCT user_id FROM selection_picks").fetchall()[0][0] == 2


def test_no_day_field(conn: sqlite3.Connection) -> None:
    for table in ("selections", "selection_picks"):
        columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert columns
        assert not [c for c in columns if "day" in c.lower()]
    for cls in (SelectionView, SelectionItem):
        assert not [f.name for f in fields(cls) if "day" in f.name.lower()]
