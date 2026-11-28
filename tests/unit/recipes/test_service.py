import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mealplanner.db.connection import connect
from mealplanner.grocery.build import _BASE, build_grocery_list
from mealplanner.grocery.info import load_grocery_info
from mealplanner.llm.errors import LLMConfigError, LLMOutputError, LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import ConfirmedPlan, MealOption, MealType
from mealplanner.planning.selection import (
    confirm_selection,
    reopen_selection,
    unpick_option,
)
from mealplanner.planning.view import load_slate_view
from mealplanner.profile.models import DEFAULT_PROFILE
from mealplanner.profile.store import save_profile
from mealplanner.recipes.errors import (
    MealNoLongerFitsError,
    RecipeGenerationError,
    RecipeNotFoundError,
    WeekNotConfirmedError,
)
from mealplanner.recipes.models import (
    GenerationReport,
    MealNoLongerFits,
    Missing,
    NoMealsPlanned,
    Recipe,
    RecipesReady,
    StepsNoLongerFit,
    WeekOpen,
)
from mealplanner.recipes.schema import RecipeDraft
from mealplanner.recipes.service import (
    generate_missing_recipes,
    get_week_recipe,
    load_recipes_view,
    regenerate_recipe,
)
from mealplanner.recipes.store import get_recipe_steps, save_recipe
from recipes.recipe_support import (
    GEN_NOW,
    LATER,
    PCAT,
    ROOT,
    WEEK,
    add_user,
    confirmed_week,
    current_plan,
    fake_for,
    open_week,
    option_ids,
    payload_with_text,
    pick,
    valid_payload,
)

INFO = load_grocery_info(PCAT, ROOT / "tests" / "fixtures" / "grocery" / "planning.csv")
LATEST = GEN_NOW + timedelta(minutes=30)
NO_PEANUTS = frozenset({ExclusionTerm.peanuts})
NO_POULTRY = frozenset({ExclusionTerm.poultry})


def no_calls() -> FakeLLMClient:
    return FakeLLMClient([])


def generate(
    conn: sqlite3.Connection,
    llm: FakeLLMClient,
    user_id: int = 1,
    now: datetime = GEN_NOW,
) -> GenerationReport:
    return generate_missing_recipes(conn, llm, PCAT, user_id, WEEK, now=now)


def view(conn: sqlite3.Connection, user_id: int = 1) -> Any:
    return load_recipes_view(conn, PCAT, user_id, WEEK)


def ready(conn: sqlite3.Connection, user_id: int = 1) -> RecipesReady:
    result = view(conn, user_id)
    assert isinstance(result, RecipesReady)
    return result


def rows(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    found = conn.execute("SELECT * FROM recipes ORDER BY slate_option_id").fetchall()
    return [tuple(r) for r in found]


def with_exclusions(conn: sqlite3.Connection, exclusions: frozenset[ExclusionTerm]) -> None:
    profile = DEFAULT_PROFILE.model_copy(update={"exclusions": exclusions})
    save_profile(conn, 1, profile, now=LATER)


def with_servings(conn: sqlite3.Connection, servings: int) -> None:
    profile = DEFAULT_PROFILE.model_copy(update={"servings": servings})
    save_profile(conn, 1, profile, now=LATER)


def generated_week(tmp_path: Path) -> tuple[sqlite3.Connection, ConfirmedPlan]:
    conn, plan = confirmed_week(tmp_path)
    generate(conn, fake_for(*plan.meals))
    return conn, plan


# --- Generation (AC-1) ---------------------------------------------------------------------


def test_one_recipe_per_meal(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path, lunches=3, dinners=2)
    llm = fake_for(*plan.meals)
    report = generate(conn, llm)
    assert len(llm.calls) == 5
    assert list(report.succeeded) == [m.option_id for m in plan.meals]
    assert report.failed == ()
    items = ready(conn).items
    assert [type(i) for i in items] == [Recipe] * 5
    assert [i.option_id for i in items] == [m.option_id for m in plan.meals]
    assert [i.title for i in items if isinstance(i, Recipe)] == [m.draft.name for m in plan.meals]
    assert len(rows(conn)) == 5


def test_generate_is_idempotent(tmp_path: Path) -> None:
    conn, _ = generated_week(tmp_path)
    before = rows(conn)
    llm = no_calls()
    report = generate(conn, llm, now=LATEST)
    assert llm.calls == []
    assert report.succeeded == ()
    assert rows(conn) == before


def test_partial_failure_keeps_successes(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    m = plan.meals
    llm = FakeLLMClient(
        [
            valid_payload(m[0]),
            valid_payload(m[1]),
            LLMUnavailableError("bridge down"),
            valid_payload(m[3]),
            valid_payload(m[4]),
        ]
    )
    report = generate(conn, llm)
    assert len(llm.calls) == 5
    assert list(report.succeeded) == [m[i].option_id for i in (0, 1, 3, 4)]
    assert len(report.failed) == 1
    failure = report.failed[0]
    assert (failure.option_id, failure.meal_name) == (m[2].option_id, m[2].draft.name)
    assert "bridge down" in failure.message
    assert len(rows(conn)) == 4
    second = fake_for(m[2])
    again = generate(conn, second, now=LATEST)
    assert len(second.calls) == 1
    assert list(again.succeeded) == [m[2].option_id]
    assert len(rows(conn)) == 5


def test_schema_failure_for_one_meal_is_recorded(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path, lunches=2, dinners=0)
    bad: dict[str, Any] = {"prep_minutes": 1, "steps": [], "title": "x"}
    llm = FakeLLMClient([bad, bad, bad, valid_payload(plan.meals[1])])
    report = generate(conn, llm)
    assert [f.option_id for f in report.failed] == [plan.meals[0].option_id]
    assert list(report.succeeded) == [plan.meals[1].option_id]


def test_rejected_meal_reported(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    with_exclusions(conn, NO_PEANUTS)
    m = plan.meals
    adversarial = payload_with_text(m[1], "Stir in some peanut butter.")
    llm = FakeLLMClient(
        [valid_payload(m[0])]
        + [adversarial] * 3
        + [valid_payload(m[2]), valid_payload(m[3]), valid_payload(m[4])]
    )
    report = generate(conn, llm)
    assert len(llm.calls) == 7
    assert [f.option_id for f in report.failed] == [m[1].option_id]
    assert "peanut" in report.failed[0].message
    assert m[1].option_id not in {r[2] for r in rows(conn)}
    items = ready(conn).items
    assert items[1] == Missing(option_id=m[1].option_id, name=m[1].draft.name)
    assert all(isinstance(i, Recipe) for i in (items[0], *items[2:]))


def test_config_error_stops(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    llm = FakeLLMClient([LLMConfigError("no bridge port"), valid_payload(plan.meals[1])])
    with pytest.raises(LLMConfigError):
        generate(conn, llm)
    assert len(llm.calls) == 1
    assert rows(conn) == []


def test_config_error_keeps_earlier_successes(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    llm = FakeLLMClient([valid_payload(plan.meals[0]), LLMConfigError("gone")])
    with pytest.raises(LLMConfigError):
        generate(conn, llm)
    assert len(llm.calls) == 2
    assert len(rows(conn)) == 1


def test_no_plan_no_call(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    pick(conn, *option_ids(conn, "lunch")[:2])
    llm = no_calls()
    with pytest.raises(WeekNotConfirmedError):
        generate(conn, llm)
    confirm_selection(conn, PCAT, 1, WEEK, now=LATER)
    reopen_selection(conn, 1, WEEK, now=LATER)
    with pytest.raises(WeekNotConfirmedError):
        generate(conn, llm)
    assert llm.calls == []
    assert rows(conn) == []


def test_no_slate_no_call(tmp_path: Path) -> None:
    conn, _ = confirmed_week(tmp_path)
    llm = no_calls()
    with pytest.raises(WeekNotConfirmedError):
        generate_missing_recipes(conn, llm, PCAT, 1, WEEK + timedelta(days=7), now=GEN_NOW)
    assert llm.calls == []


def test_unavailable_no_call(tmp_path: Path) -> None:
    conn, _ = confirmed_week(tmp_path)
    with_exclusions(conn, NO_POULTRY)
    llm = no_calls()
    with pytest.raises(MealNoLongerFitsError):
        generate(conn, llm)
    assert llm.calls == []
    assert rows(conn) == []


def test_empty_plan_no_call(tmp_path: Path) -> None:
    conn = open_week(tmp_path)
    confirm_selection(conn, PCAT, 1, WEEK, now=LATER)
    llm = no_calls()
    report = generate(conn, llm)
    assert (report.succeeded, report.failed) == ((), ())
    assert llm.calls == []
    assert isinstance(view(conn), NoMealsPlanned)


# --- Consistency (AC-2, AC-3) --------------------------------------------------------------


def lines(meal: MealOption) -> list[tuple[str, float, str]]:
    return [(i.ingredient_id, i.quantity, i.unit.value) for i in meal.draft.ingredients]


def test_ingredients_equal_meal_scaled(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    for meal, recipe in zip(plan.meals, ready(conn).items, strict=True):
        assert isinstance(recipe, Recipe)
        assert recipe.servings == 2
        got = [(i.ingredient_id, i.quantity, i.unit.value) for i in recipe.ingredients]
        assert got == lines(meal)
    with_servings(conn, 3)
    for meal, recipe in zip(plan.meals, ready(conn).items, strict=True):
        assert isinstance(recipe, Recipe)
        assert recipe.servings == 3
        want = [q * 1.5 for _, q, _ in lines(meal)]
        assert [i.quantity for i in recipe.ingredients] == pytest.approx(want)
    llm = no_calls()
    assert generate(conn, llm, now=LATEST).succeeded == ()
    assert llm.calls == []


@pytest.mark.parametrize("servings", [2, 3])
def test_every_ingredient_on_grocery_list(tmp_path: Path, servings: int) -> None:
    conn, _ = generated_week(tmp_path)
    if servings != 2:
        with_servings(conn, servings)
    plan = current_plan(conn)
    grocery = build_grocery_list(plan.meals, catalog=PCAT, grocery_info=INFO, servings=servings)
    items = {i.ingredient_id: i for s in grocery.sections for i in s.items}
    for recipe in ready(conn).items:
        assert isinstance(recipe, Recipe)
        for line in recipe.ingredients:
            dimension, base = _BASE[line.unit]
            need = {n.dimension: n.amount for n in items[line.ingredient_id].needs}[dimension]
            assert line.quantity * base <= need + 1e-9


def test_nutrition_equals_selection(tmp_path: Path) -> None:
    conn, _ = generated_week(tmp_path)
    slate = load_slate_view(conn, PCAT, 1, WEEK)
    assert slate is not None
    shown = {
        s.option_id: s for mt in MealType for s in slate.options[mt] if isinstance(s, MealOption)
    }
    for recipe in ready(conn).items:
        assert isinstance(recipe, Recipe)
        assert recipe.nutrition == shown[recipe.option_id].nutrition


# --- Regenerate (AC-6) ---------------------------------------------------------------------


def test_regenerate_changes_only_one(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    before = rows(conn)
    target = plan.meals[1]
    llm = FakeLLMClient([valid_payload(target, prep=3)])
    recipe = regenerate_recipe(conn, llm, PCAT, 1, target.option_id, now=LATEST)
    assert recipe.prep_minutes == 3
    after = rows(conn)
    changed = [b[2] for b, a in zip(before, after, strict=True) if a != b]
    assert changed == [target.option_id]
    old_row = next(b for b in before if b[2] == target.option_id)
    new_row = next(a for a in after if a[2] == target.option_id)
    assert new_row[3] != old_row[3]
    assert new_row[4] != old_row[4]


def test_regenerate_same_checks(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    with_exclusions(conn, NO_PEANUTS)
    target = plan.meals[1]
    before = rows(conn)
    adversarial = payload_with_text(target, "Stir in some peanut butter.")
    with pytest.raises(RecipeGenerationError):
        regenerate_recipe(
            conn, FakeLLMClient([adversarial] * 3), PCAT, 1, target.option_id, now=LATEST
        )
    assert rows(conn) == before
    invalid: dict[str, Any] = {"prep_minutes": 1, "steps": []}
    with pytest.raises(LLMOutputError):
        regenerate_recipe(conn, FakeLLMClient([invalid] * 3), PCAT, 1, target.option_id, now=LATEST)
    assert rows(conn) == before


def test_regenerate_without_stored_recipe_creates_it(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    target = plan.meals[0]
    regenerate_recipe(conn, fake_for(target), PCAT, 1, target.option_id, now=GEN_NOW)
    assert [r[2] for r in rows(conn)] == [target.option_id]


def test_regenerate_outside_plan(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    add_user(conn, 2)
    pick(conn, *option_ids(conn, "lunch", 2)[:1], user_id=2)
    picked_ids = {m.option_id for m in plan.meals}
    unpicked = next(i for i in option_ids(conn, "lunch") if i not in picked_ids)
    other_users = option_ids(conn, "lunch", 2)[0]
    llm = no_calls()
    for option in (unpicked, other_users, 99999):
        with pytest.raises(RecipeNotFoundError):
            regenerate_recipe(conn, llm, PCAT, 1, option, now=LATEST)
    assert llm.calls == []


def test_regenerate_in_open_week(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    llm = no_calls()
    for meal in plan.meals:
        with pytest.raises(RecipeNotFoundError):
            regenerate_recipe(conn, llm, PCAT, 1, meal.option_id, now=LATEST)
    assert llm.calls == []


def test_regenerate_unpicked_after_reconfirm(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    gone = plan.meals[0].option_id
    unpick_option(conn, 1, gone, now=LATEST)
    confirm_selection(conn, PCAT, 1, WEEK, now=LATEST)
    llm = no_calls()
    with pytest.raises(RecipeNotFoundError):
        regenerate_recipe(conn, llm, PCAT, 1, gone, now=LATEST)
    assert llm.calls == []


def test_regenerate_unavailable_week(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    with_exclusions(conn, NO_POULTRY)
    llm = no_calls()
    with pytest.raises(MealNoLongerFitsError):
        regenerate_recipe(conn, llm, PCAT, 1, plan.meals[0].option_id, now=LATEST)
    assert llm.calls == []


# --- Reading (P9, ADR 0021) ----------------------------------------------------------------


def test_week_open_withholds(tmp_path: Path) -> None:
    conn, _ = generated_week(tmp_path)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    assert len(rows(conn)) == 5
    assert isinstance(view(conn), WeekOpen)


def test_no_slate_is_week_open(tmp_path: Path) -> None:
    conn, _ = confirmed_week(tmp_path)
    assert isinstance(load_recipes_view(conn, PCAT, 1, WEEK + timedelta(days=7)), WeekOpen)


def test_unsafe_withholds_all(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    with_exclusions(conn, NO_POULTRY)
    result = view(conn)
    assert isinstance(result, MealNoLongerFits)
    assert not hasattr(result, "items")
    assert result.unavailable
    with pytest.raises(MealNoLongerFitsError):
        get_week_recipe(conn, PCAT, 1, WEEK, plan.meals[0].option_id)


def test_steps_fail_on_read(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    target, other = plan.meals[0], plan.meals[1]
    text = "Stir in a spoon of peanut butter at the end."
    stale = RecipeDraft.model_validate(payload_with_text(target, text))
    save_recipe(conn, 1, target.option_id, stale, now=GEN_NOW)
    fine = RecipeDraft.model_validate(valid_payload(other))
    save_recipe(conn, 1, other.option_id, fine, now=GEN_NOW)
    assert isinstance(ready(conn).items[0], Recipe)
    with_exclusions(conn, NO_PEANUTS)
    items = ready(conn).items
    assert isinstance(items[0], StepsNoLongerFit)
    assert (items[0].option_id, items[0].name) == (target.option_id, target.draft.name)
    assert items[0].reason
    assert not hasattr(items[0], "steps")
    assert isinstance(items[1], Recipe)
    assert isinstance(get_week_recipe(conn, PCAT, 1, WEEK, target.option_id), StepsNoLongerFit)
    # Generating again replaces the stored steps that no longer pass, and only those.
    llm = fake_for(target, *plan.meals[2:])
    report = generate(conn, llm, now=LATEST)
    assert target.option_id in report.succeeded
    assert other.option_id not in report.succeeded
    assert isinstance(ready(conn).items[0], Recipe)


def test_stored_steps_that_break_the_schema_fail_on_read(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    target = plan.meals[0]
    junk = RecipeDraft.model_validate(
        {"prep_minutes": 1, "steps": [{"text": "Do it.", "ingredient_ids": ["not_in_meal"]}]}
    )
    save_recipe(conn, 1, target.option_id, junk, now=GEN_NOW)
    assert isinstance(ready(conn).items[0], StepsNoLongerFit)


def test_corrupt_stored_data_fails_on_read(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    conn.execute(
        "INSERT INTO recipes (user_id, slate_option_id, data, generated_at) VALUES (1, ?, ?, ?)",
        (plan.meals[0].option_id, "not json", "2026-11-01T12:00:00Z"),
    )
    conn.commit()
    assert isinstance(ready(conn).items[0], StepsNoLongerFit)


def test_reconfirm_unchanged_reuses(tmp_path: Path) -> None:
    conn, _ = generated_week(tmp_path)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    confirm_selection(conn, PCAT, 1, WEEK, now=LATEST)
    assert [type(i) for i in ready(conn).items] == [Recipe] * 5
    llm = no_calls()
    assert generate(conn, llm, now=LATEST).succeeded == ()
    assert llm.calls == []


def test_reconfirm_swapped(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    dropped = plan.meals[1].option_id
    unpick_option(conn, 1, dropped, now=LATEST)
    picked = {m.option_id for m in plan.meals}
    new_id = next(i for i in option_ids(conn, "lunch") if i not in picked)
    pick(conn, new_id)
    confirm_selection(conn, PCAT, 1, WEEK, now=LATEST)
    items = ready(conn).items
    assert [i.option_id for i in items if isinstance(i, Missing)] == [new_id]
    assert len([i for i in items if isinstance(i, Recipe)]) == 4
    assert dropped not in [i.option_id for i in items]
    new_meal = next(m for m in current_plan(conn).meals if m.option_id == new_id)
    llm = fake_for(new_meal)
    generate(conn, llm, now=LATEST)
    assert len(llm.calls) == 1
    assert [type(i) for i in ready(conn).items] == [Recipe] * 5


def test_old_recipe_not_shown(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    dropped = plan.meals[0].option_id
    unpick_option(conn, 1, dropped, now=LATEST)
    confirm_selection(conn, PCAT, 1, WEEK, now=LATEST)
    assert len(rows(conn)) == 5
    assert dropped not in [i.option_id for i in ready(conn).items]
    with pytest.raises(RecipeNotFoundError):
        get_week_recipe(conn, PCAT, 1, WEEK, dropped)


def test_get_week_recipe_states(tmp_path: Path) -> None:
    conn, plan = confirmed_week(tmp_path)
    first, second = plan.meals[0], plan.meals[1]
    missing = get_week_recipe(conn, PCAT, 1, WEEK, first.option_id)
    assert missing == Missing(option_id=first.option_id, name=first.draft.name)
    generate(conn, fake_for(*plan.meals))
    one = get_week_recipe(conn, PCAT, 1, WEEK, second.option_id)
    assert isinstance(one, Recipe)
    assert one == ready(conn).items[1]
    with pytest.raises(RecipeNotFoundError):
        get_week_recipe(conn, PCAT, 1, WEEK, 99999)
    reopen_selection(conn, 1, WEEK, now=LATEST)
    with pytest.raises(RecipeNotFoundError):
        get_week_recipe(conn, PCAT, 1, WEEK, second.option_id)


def test_second_user_isolated(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    add_user(conn, 2)
    pick(conn, *option_ids(conn, "lunch", 2)[:2], user_id=2)
    confirm_selection(conn, PCAT, 2, WEEK, now=LATER)
    plan2 = current_plan(conn, 2)
    assert {m.option_id for m in plan.meals}.isdisjoint(m.option_id for m in plan2.meals)
    # Their view starts empty: none of user 1's recipes leak into it.
    assert [type(i) for i in ready(conn, 2).items] == [Missing, Missing]
    llm = no_calls()
    with pytest.raises(RecipeNotFoundError):
        regenerate_recipe(conn, llm, PCAT, 2, plan.meals[0].option_id, now=LATEST)
    with pytest.raises(RecipeNotFoundError):
        get_week_recipe(conn, PCAT, 2, WEEK, plan.meals[0].option_id)
    assert llm.calls == []
    before = rows(conn)
    generate(conn, fake_for(*plan2.meals), user_id=2, now=LATEST)
    assert [type(i) for i in ready(conn, 2).items] == [Recipe, Recipe]
    after = rows(conn)
    assert [r for r in after if r[1] == 1] == before
    assert {r[1] for r in after} == {1, 2}
    assert [type(i) for i in ready(conn).items] == [Recipe] * 5


def test_survives_reconnect(tmp_path: Path) -> None:
    conn, plan = generated_week(tmp_path)
    conn.close()
    again = connect(tmp_path / "app.sqlite3")
    try:
        assert [type(i) for i in ready(again).items] == [Recipe] * 5
        assert get_recipe_steps(again, 1, plan.meals[0].option_id) is not None
    finally:
        again.close()


def test_offline(tmp_path: Path) -> None:
    """AC-7: only the fake client is called; the autouse guard fails any network use."""
    conn, plan = confirmed_week(tmp_path)
    llm = fake_for(*plan.meals)
    generate(conn, llm)
    assert len(llm.calls) == 5
