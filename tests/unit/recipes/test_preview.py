"""Recipe previews during selection (#157, ADR 0026): preview_recipe and get_option_recipe."""

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mealplanner.llm.errors import LLMOutputError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.errors import OptionUnavailableError
from mealplanner.planning.models import MealOption
from mealplanner.planning.selection import confirm_selection
from mealplanner.planning.view import load_slate_view
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile
from mealplanner.recipes.errors import RecipeGenerationError, RecipeNotFoundError
from mealplanner.recipes.models import Missing, Recipe, StepsNoLongerFit
from mealplanner.recipes.schema import RecipeDraft
from mealplanner.recipes.service import (
    generate_missing_recipes,
    get_option_recipe,
    preview_recipe,
)
from mealplanner.recipes.store import save_recipe
from recipes.recipe_support import (
    GEN_NOW,
    LATER,
    PCAT,
    WEEK,
    add_user,
    open_week,
    option_ids,
    payload_with_text,
    pick,
    valid_payload,
)

NO_PEANUTS = frozenset({ExclusionTerm.peanuts})
NO_POULTRY = frozenset({ExclusionTerm.poultry})
LATEST = GEN_NOW + timedelta(minutes=30)


def meal_of(conn: sqlite3.Connection, option_id: int) -> MealOption:
    view = load_slate_view(conn, PCAT, 1, WEEK)
    assert view is not None
    for slots in view.options.values():
        for slot in slots:
            if isinstance(slot, MealOption) and slot.option_id == option_id:
                return slot
    raise AssertionError(option_id)


def rows(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    found = conn.execute("SELECT * FROM recipes ORDER BY slate_option_id").fetchall()
    return [tuple(r) for r in found]


def preview(conn: sqlite3.Connection, llm: FakeLLMClient, option_id: int, user_id: int = 1) -> Any:
    return preview_recipe(conn, llm, PCAT, user_id, WEEK, option_id, now=GEN_NOW)


def read(conn: sqlite3.Connection, option_id: int, user_id: int = 1) -> Any:
    return get_option_recipe(conn, PCAT, user_id, WEEK, option_id)


def with_exclusions(conn: sqlite3.Connection, exclusions: frozenset[ExclusionTerm]) -> None:
    profile = DEFAULT_PROFILE.model_copy(update={"exclusions": exclusions})
    save_profile(conn, 1, profile, now=LATER)


def first_lunch(conn: sqlite3.Connection) -> tuple[int, MealOption]:
    oid = option_ids(conn, "lunch")[0]
    return oid, meal_of(conn, oid)


def test_preview_generates_and_stores(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    llm = FakeLLMClient([valid_payload(meal)])
    recipe = preview(conn, llm, oid)
    assert len(llm.calls) == 1
    assert isinstance(recipe, Recipe)
    assert [r[2] for r in rows(conn)] == [oid]
    # AC-3: the builder takes the meal's own value. The test's meal is a separate load of the
    # slate, so the check is equality, not identity.
    assert recipe.nutrition == meal.nutrition


def test_preview_reuses_stored(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    first = preview(conn, FakeLLMClient([valid_payload(meal)]), oid)
    before = rows(conn)
    llm = FakeLLMClient([])
    second = preview_recipe(conn, llm, PCAT, 1, WEEK, oid, now=LATEST)
    assert llm.calls == []
    assert rows(conn) == before
    assert second == first


def test_get_option_recipe_no_call(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    assert read(conn, oid) == Missing(option_id=oid, name=meal.draft.name)
    preview(conn, FakeLLMClient([valid_payload(meal)]), oid)
    assert isinstance(read(conn, oid), Recipe)


def test_preview_unpicked_option_allowed(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    pick(conn, option_ids(conn, "lunch")[1])
    assert isinstance(preview(conn, FakeLLMClient([valid_payload(meal)]), oid), Recipe)


def test_preview_while_confirmed_allowed(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    pick(conn, option_ids(conn, "lunch")[1])
    confirm_selection(conn, PCAT, 1, WEEK, now=LATER)
    assert isinstance(preview(conn, FakeLLMClient([valid_payload(meal)]), oid), Recipe)


def test_preview_not_found(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    add_user(conn, 2)
    oid, _ = first_lunch(conn)
    replaced = option_ids(conn, "lunch")[1]
    conn.execute("UPDATE slate_options SET replaced_at = ? WHERE id = ?", ("2026-01-01", replaced))
    theirs = option_ids(conn, "lunch", 2)[0]
    llm = FakeLLMClient([])
    for target, user in ((999999, 1), (replaced, 1), (theirs, 1), (oid, 2)):
        with pytest.raises(RecipeNotFoundError):
            preview(conn, llm, target, user)
        with pytest.raises(RecipeNotFoundError):
            read(conn, target, user)
    with pytest.raises(RecipeNotFoundError):  # another week's slate
        preview_recipe(conn, llm, PCAT, 1, WEEK + timedelta(days=7), oid, now=GEN_NOW)
    with pytest.raises(RecipeNotFoundError):
        get_option_recipe(conn, PCAT, 1, WEEK + timedelta(days=7), oid)
    assert llm.calls == []


def test_preview_unavailable(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    meals = [meal_of(conn, i) for i in option_ids(conn, "lunch")]
    meal = next(m for m in meals if any(i.ingredient_id == "chicken" for i in m.draft.ingredients))
    oid = meal.option_id
    assert oid is not None
    preview(conn, FakeLLMClient([valid_payload(meal)]), oid)
    with_exclusions(conn, NO_POULTRY)
    llm = FakeLLMClient([])
    with pytest.raises(OptionUnavailableError):
        preview(conn, llm, oid)
    with pytest.raises(OptionUnavailableError):
        read(conn, oid)
    assert llm.calls == []
    assert len(rows(conn)) == 1


def test_preview_steps_fail_on_read(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    stale = RecipeDraft.model_validate(
        payload_with_text(meal, "Stir in a spoon of peanut butter at the end.")
    )
    save_recipe(conn, 1, oid, stale, now=GEN_NOW)
    assert isinstance(read(conn, oid), Recipe)
    with_exclusions(conn, NO_PEANUTS)
    result = read(conn, oid)
    assert isinstance(result, StepsNoLongerFit)
    assert not hasattr(result, "steps")
    llm = FakeLLMClient([valid_payload(meal)])
    assert isinstance(preview_recipe(conn, llm, PCAT, 1, WEEK, oid, now=LATEST), Recipe)
    assert len(llm.calls) == 1
    assert isinstance(read(conn, oid), Recipe)


def test_preview_rejected_never_stored(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    with_exclusions(conn, NO_PEANUTS)
    oid, meal = first_lunch(conn)
    bad = payload_with_text(meal, "Stir in some peanut butter.")
    with pytest.raises(RecipeGenerationError):
        preview(conn, FakeLLMClient([bad] * 3), oid)
    assert rows(conn) == []


def test_preview_schema_invalid_never_stored(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, _ = first_lunch(conn)
    invalid: dict[str, Any] = {"prep_minutes": 1, "steps": []}
    with pytest.raises(LLMOutputError):
        preview(conn, FakeLLMClient([invalid] * 3), oid)
    assert rows(conn) == []


def test_preview_reused_after_confirm(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    x, y = option_ids(conn, "lunch")[:2]
    mx, my = meal_of(conn, x), meal_of(conn, y)
    preview(conn, FakeLLMClient([valid_payload(mx)]), x)
    stored = rows(conn)
    pick(conn, x, y)
    confirm_selection(conn, PCAT, 1, WEEK, now=LATER)
    llm = FakeLLMClient([valid_payload(my)])
    report = generate_missing_recipes(conn, llm, PCAT, 1, WEEK, now=LATEST)
    assert len(llm.calls) == 1
    assert list(report.succeeded) == [y]
    assert [r for r in rows(conn) if r[2] == x] == stored


def test_replaced_preview_not_shown(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    preview(conn, FakeLLMClient([valid_payload(meal)]), oid)
    row = conn.execute("SELECT * FROM slate_options WHERE id = ?", (oid,)).fetchone()
    conn.execute("UPDATE slate_options SET replaced_at = ? WHERE id = ?", ("2026-01-01", oid))
    cols = [c for c in row.keys() if c not in ("id", "replaced_at")]  # noqa: SIM118
    conn.execute(
        f"INSERT INTO slate_options ({', '.join(cols)}) "  # noqa: S608
        f"SELECT {', '.join(cols)} FROM slate_options WHERE id = ?",
        (oid,),
    )
    new_id = int(conn.execute("SELECT MAX(id) FROM slate_options").fetchone()[0])
    assert new_id != oid
    llm = FakeLLMClient([])
    with pytest.raises(RecipeNotFoundError):
        preview(conn, llm, oid)
    assert [r[2] for r in rows(conn)] == [oid]
    assert isinstance(read(conn, new_id), Missing)
    assert llm.calls == []


def test_preview_scaled_to_current_servings(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    before = preview(conn, FakeLLMClient([valid_payload(meal)]), oid)
    bigger = DEFAULT_PROFILE.model_copy(update={"servings": DEFAULT_PROFILE.servings + 2})
    save_profile(conn, 1, bigger, now=LATER)
    after = read(conn, oid)
    assert isinstance(after, Recipe)
    assert after.servings == bigger.servings
    assert after.ingredients[0].quantity > before.ingredients[0].quantity
    assert after.nutrition == before.nutrition


def test_second_user_isolated(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    add_user(conn, 2)
    oid, meal = first_lunch(conn)
    preview(conn, FakeLLMClient([valid_payload(meal)]), oid)
    theirs = option_ids(conn, "lunch", 2)[0]
    assert isinstance(read(conn, theirs, 2), Missing)
    with pytest.raises(RecipeNotFoundError):
        read(conn, oid, 2)
    assert [r[1] for r in rows(conn)] == [1]


def test_offline(tmp_path: Path) -> None:
    """AC-7: only the fake client is called; the autouse guard fails any network use."""
    conn = open_week(tmp_path)
    oid, meal = first_lunch(conn)
    llm = FakeLLMClient([valid_payload(meal)])
    preview(conn, llm, oid)
    assert len(llm.calls) == 1
