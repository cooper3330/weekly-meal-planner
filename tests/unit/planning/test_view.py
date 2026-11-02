import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from slate_support import CAT, DINNER_START, NOW, WEEK, batch, open_db, option, plain_batch

from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.compute import compute_nutrition
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.errors import ProfileRequiredError
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.models import MealOption, MealType, UnavailableOption
from mealplanner.planning.store import get_slate
from mealplanner.planning.view import load_slate_view
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = open_db(tmp_path)
    save_profile(c, 1, DEFAULT_PROFILE, now=NOW)
    yield c
    c.close()


def generate(conn: sqlite3.Connection, lunches: list[dict[str, object]] | None = None) -> None:
    first = batch(*lunches) if lunches else plain_batch(0, 3)
    generate_slate(
        conn,
        FakeLLMClient([first, plain_batch(DINNER_START, 2)]),
        CAT,
        1,
        week_start=WEEK,
        now=NOW,
        lunch_count=3,
        dinner_count=2,
    )


def test_no_slate_is_none(conn: sqlite3.Connection) -> None:
    assert load_slate_view(conn, CAT, 1, WEEK) is None


def test_view_nutrition_equals_compute(conn: sqlite3.Connection) -> None:
    generate(conn)
    view = load_slate_view(conn, CAT, 1, WEEK)
    assert view is not None
    stored = get_slate(conn, 1, WEEK)
    assert stored is not None
    for mt in MealType:
        assert len(view.options[mt]) == len(stored.options[mt])
        for slot, row in zip(view.options[mt], stored.options[mt], strict=True):
            assert isinstance(slot, MealOption)
            assert slot.option_id == row.id
            assert slot.servings == row.servings
            expected = compute_nutrition(
                [(i.ingredient_id, i.quantity, i.unit) for i in row.draft.ingredients],
                CAT,
                row.servings,
            )
            assert slot.nutrition == expected


def test_view_uses_stored_servings(conn: sqlite3.Connection) -> None:
    generate(conn)
    save_profile(conn, 1, DEFAULT_PROFILE.model_copy(update={"servings": 5}), now=NOW)
    view = load_slate_view(conn, CAT, 1, WEEK)
    assert view is not None
    slot = view.options[MealType.lunch][0]
    assert isinstance(slot, MealOption)
    assert slot.servings == 2


def test_view_hides_newly_excluded(conn: sqlite3.Connection) -> None:
    soy = option(5, name="Soy Glazed Bowl", extra=[("soy_sauce", 1, "tbsp")])
    generate(conn, [option(1), option(2), soy])
    save_profile(
        conn,
        1,
        DEFAULT_PROFILE.model_copy(update={"exclusions": frozenset({ExclusionTerm.soy})}),
        now=NOW,
    )
    view = load_slate_view(conn, CAT, 1, WEEK)
    assert view is not None
    lunches = view.options[MealType.lunch]
    assert isinstance(lunches[0], MealOption)
    assert isinstance(lunches[1], MealOption)
    hidden = lunches[2]
    assert isinstance(hidden, UnavailableOption)
    assert "soy" in hidden.reason
    stored = get_slate(conn, 1, WEEK)
    assert stored is not None
    assert hidden.option_id == stored.options[MealType.lunch][2].id


def test_view_hides_when_cook_time_lowered(conn: sqlite3.Connection) -> None:
    generate(conn)
    save_profile(conn, 1, DEFAULT_PROFILE.model_copy(update={"max_cook_minutes": 10}), now=NOW)
    view = load_slate_view(conn, CAT, 1, WEEK)
    assert view is not None
    assert all(isinstance(o, UnavailableOption) for mt in MealType for o in view.options[mt])


def test_corrupt_row_raises(conn: sqlite3.Connection) -> None:
    generate(conn)
    conn.execute("UPDATE slate_options SET data = '{\"name\": 1}' WHERE id = 2")
    conn.commit()
    with pytest.raises(ValueError):
        load_slate_view(conn, CAT, 1, WEEK)


def test_other_user_sees_nothing(conn: sqlite3.Connection) -> None:
    generate(conn)
    conn.execute("INSERT INTO users (id, created_at) VALUES (2, '2026-01-01T00:00:00Z')")
    conn.commit()
    assert load_slate_view(conn, CAT, 2, WEEK) is None


def test_slate_without_profile_raises(conn: sqlite3.Connection) -> None:
    generate(conn)
    conn.execute("DELETE FROM profile_versions")
    conn.commit()
    with pytest.raises(ProfileRequiredError):
        load_slate_view(conn, CAT, 1, WEEK)
