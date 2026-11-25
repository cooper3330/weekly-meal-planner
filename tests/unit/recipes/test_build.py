import pytest

from mealplanner.nutrition.compute import display_nutrition
from mealplanner.nutrition.units import Unit
from mealplanner.planning.models import Rejection
from mealplanner.recipes.build import build_recipe
from mealplanner.recipes.checks import check_recipe
from mealplanner.recipes.models import RecipeSteps
from mealplanner.recipes.schema import recipe_draft_model
from recipes.recipe_support import CAT, make_meal, valid_payload

# Awkward floats on purpose: 0.1 * 3 / 3 is not 0.1.
MEAL = make_meal(("chicken", "olive_oil"), servings=3, quantity=0.1, option_id=7, cook_minutes=40)


def steps_for(prep: int = 10) -> RecipeSteps:
    draft = recipe_draft_model(MEAL, catalog=CAT).model_validate(valid_payload(MEAL, prep=prep))
    result = check_recipe(draft, meal=MEAL, catalog=CAT, exclusions=frozenset())
    assert not isinstance(result, Rejection)
    return result


def test_lines_equal_meal_lines() -> None:
    recipe = build_recipe(MEAL, steps_for(), profile_servings=MEAL.servings, catalog=CAT)
    assert [(i.ingredient_id, i.quantity, i.unit) for i in recipe.ingredients] == [
        (line.ingredient_id, line.quantity, line.unit) for line in MEAL.draft.ingredients
    ]
    assert [i.name for i in recipe.ingredients] == [
        CAT.get(line.ingredient_id).name for line in MEAL.draft.ingredients
    ]


def test_scaled_up_and_down() -> None:
    meal = make_meal(("chicken", "olive_oil"), servings=2, quantity=50)
    draft = recipe_draft_model(meal, catalog=CAT).model_validate(valid_payload(meal))
    steps = check_recipe(draft, meal=meal, catalog=CAT, exclusions=frozenset())
    assert isinstance(steps, RecipeSteps)
    base = [line.quantity for line in meal.draft.ingredients]
    up = build_recipe(meal, steps, profile_servings=4, catalog=CAT)
    down = build_recipe(meal, steps, profile_servings=1, catalog=CAT)
    assert [i.quantity for i in up.ingredients] == [q * 2 for q in base]
    assert [i.quantity for i in down.ingredients] == [q / 2 for q in base]
    assert [i.unit for i in up.ingredients] == [line.unit for line in meal.draft.ingredients]
    assert up.servings == 4
    assert down.servings == 1


def test_units_stay_the_meals_own() -> None:
    meal = make_meal(("olive_oil",), unit="tbsp", quantity=2)
    draft = recipe_draft_model(meal, catalog=CAT).model_validate(valid_payload(meal))
    steps = check_recipe(draft, meal=meal, catalog=CAT, exclusions=frozenset())
    assert isinstance(steps, RecipeSteps)
    recipe = build_recipe(meal, steps, profile_servings=6, catalog=CAT)
    assert recipe.ingredients[-1].unit is Unit.tbsp
    assert recipe.ingredients[-1].quantity == 6.0


@pytest.mark.parametrize("servings", [1, 2, 3, 12])
def test_nutrition_is_meals(servings: int) -> None:
    recipe = build_recipe(MEAL, steps_for(), profile_servings=servings, catalog=CAT)
    assert recipe.nutrition is MEAL.nutrition
    assert display_nutrition(recipe.nutrition) == display_nutrition(MEAL.nutrition)


@pytest.mark.parametrize("prep", [0, 15, 40])
def test_times(prep: int) -> None:
    recipe = build_recipe(MEAL, steps_for(prep), profile_servings=2, catalog=CAT)
    assert recipe.total_minutes == MEAL.draft.cook_minutes == 40
    assert recipe.prep_minutes == prep
    assert recipe.prep_minutes + recipe.cook_minutes == recipe.total_minutes
    assert recipe.cook_minutes == 40 - prep


def test_title_from_meal() -> None:
    recipe = build_recipe(MEAL, steps_for(), profile_servings=2, catalog=CAT)
    assert recipe.title == MEAL.draft.name
    assert recipe.description == MEAL.draft.description
    assert recipe.option_id == 7
    assert [s.text for s in recipe.steps] == [s.text for s in steps_for().steps]
    assert not hasattr(steps_for(), "title")


@pytest.mark.parametrize("servings", [0, -1])
def test_invalid_servings(servings: int) -> None:
    with pytest.raises(ValueError):
        build_recipe(MEAL, steps_for(), profile_servings=servings, catalog=CAT)


def test_prep_over_total_is_refused() -> None:
    steps = RecipeSteps(prep_minutes=41, steps=steps_for().steps)
    with pytest.raises(ValueError):
        build_recipe(MEAL, steps, profile_servings=2, catalog=CAT)
