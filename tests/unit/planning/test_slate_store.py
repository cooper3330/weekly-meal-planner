import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from slate_support import CAT, NOW, WEEK, count_rows, open_db, option

from mealplanner.planning.checks import check_option
from mealplanner.planning.errors import SlateExistsError
from mealplanner.planning.models import MealOption, MealType
from mealplanner.planning.schema import MealOptionDraft
from mealplanner.planning.store import get_slate, recent_meals, save_slate


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = open_db(tmp_path)
    c.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    c.commit()
    yield c
    c.close()


def built(index: int, servings: int = 2, **kw: object) -> MealOption:
    d = MealOptionDraft.model_validate(option(index, **kw))  # type: ignore[arg-type]
    result = check_option(
        d, catalog=CAT, servings=servings, max_cook_minutes=60, exclusions=frozenset()
    )
    assert isinstance(result, MealOption)
    return result


def options(lunches: int = 3, dinners: int = 2) -> dict[MealType, list[MealOption]]:
    return {
        MealType.lunch: [built(i) for i in range(lunches)],
        MealType.dinner: [built(32 + i) for i in range(dinners)],
    }


def test_save_and_get_round_trip(conn: sqlite3.Connection) -> None:
    opts = options()
    slate_id = save_slate(conn, 1, WEEK, 1, opts, now=NOW)
    stored = get_slate(conn, 1, WEEK)
    assert stored is not None
    assert stored.id == slate_id
    assert stored.week_start == WEEK
    assert stored.profile_version == 1
    for mt in MealType:
        got = stored.options[mt]
        assert [o.position for o in got] == list(range(1, len(opts[mt]) + 1))
        assert [o.draft.name for o in got] == [o.draft.name for o in opts[mt]]
        assert all(o.servings == 2 for o in got)
        assert all(o.id > 0 for o in got)
        assert [o.draft for o in got] == [
            MealOptionDraft.model_validate(o.draft.model_dump()) for o in opts[mt]
        ]
    for (data,) in conn.execute("SELECT data FROM slate_options"):
        keys = set(json.loads(data))
        assert keys == {"name", "description", "cook_minutes", "tags", "ingredients"}
        assert "kcal" not in data and "nutrition" not in data and "calories" not in data


def test_get_slate_missing_is_none(conn: sqlite3.Connection) -> None:
    assert get_slate(conn, 1, WEEK) is None


def test_second_slate_same_week_raises_and_leaves_first(conn: sqlite3.Connection) -> None:
    save_slate(conn, 1, WEEK, 1, options(), now=NOW)
    before = count_rows(conn)
    with pytest.raises(SlateExistsError):
        save_slate(conn, 1, WEEK, 1, options(), now=NOW)
    assert count_rows(conn) == before


def test_other_week_allowed(conn: sqlite3.Connection) -> None:
    save_slate(conn, 1, WEEK, 1, options(), now=NOW)
    save_slate(conn, 1, date(2026, 11, 9), 1, options(), now=NOW)


def test_users_are_separate(conn: sqlite3.Connection) -> None:
    save_slate(conn, 1, WEEK, 1, options(), now=NOW)
    assert get_slate(conn, 2, WEEK) is None
    assert recent_meals(conn, 2, date(2026, 11, 9), 4) == []
    assert len(recent_meals(conn, 1, date(2026, 11, 9), 4)) == 5
    # User 2 may have their own slate for the same week.
    save_slate(conn, 2, WEEK, 1, options(1, 1), now=NOW)
    assert len(get_slate(conn, 2, WEEK).options[MealType.lunch]) == 1  # type: ignore[union-attr]


def test_recent_meals_window(conn: sqlite3.Connection) -> None:
    save_slate(conn, 1, date(2026, 10, 12), 1, options(1, 0), now=NOW)  # 3 weeks before
    save_slate(conn, 1, date(2026, 10, 26), 1, options(0, 1), now=NOW)  # 1 week before
    target = date(2026, 11, 2)
    assert recent_meals(conn, 1, target, 0) == []
    one = recent_meals(conn, 1, target, 1)
    assert [m.name for m in one] == ["Test meal number 32"]
    assert "dense" in one[0].ingredient_ids
    assert len(recent_meals(conn, 1, target, 2)) == 1
    assert len(recent_meals(conn, 1, target, 3)) == 2
    # The target week itself is not recent.
    assert [m.name for m in recent_meals(conn, 1, date(2026, 10, 26), 5)] == ["Test meal number 0"]


def test_recent_meals_include_replaced_options(conn: sqlite3.Connection) -> None:
    save_slate(conn, 1, date(2026, 10, 26), 1, options(1, 0), now=NOW)
    conn.execute("UPDATE slate_options SET replaced_at = '2026-10-27T00:00:00Z'")
    conn.commit()
    assert len(recent_meals(conn, 1, WEEK, 1)) == 1
    stored = get_slate(conn, 1, date(2026, 10, 26))
    assert stored is not None
    assert stored.options[MealType.lunch] == []


def test_corrupt_row_raises(conn: sqlite3.Connection) -> None:
    save_slate(conn, 1, WEEK, 1, options(), now=NOW)
    conn.execute("UPDATE slate_options SET data = '{not json' WHERE id = 1")
    conn.commit()
    with pytest.raises(ValueError):
        get_slate(conn, 1, WEEK)
    with pytest.raises(ValueError):
        recent_meals(conn, 1, date(2026, 11, 9), 1)


def test_unknown_user_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        save_slate(conn, 999, WEEK, 1, options(), now=NOW)
    assert count_rows(conn) == 0


def test_naive_now_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        save_slate(conn, 1, WEEK, 1, options(), now=NOW.replace(tzinfo=None))
    assert count_rows(conn) == 0


def test_no_day_column(conn: sqlite3.Connection) -> None:
    for table in ("slates", "slate_options"):
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert not [c for c in cols if "day" in c]
