from typing import Any

import pytest

from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import MealOption, Rejection
from mealplanner.recipes.checks import check_recipe
from mealplanner.recipes.models import RecipeSteps
from mealplanner.recipes.schema import RecipeDraft, RecipeStep, recipe_draft_model
from recipes.recipe_support import CAT, make_meal, payload_with_text, valid_payload

MEAL = make_meal()


def draft_of(meal: MealOption, payload: dict[str, Any]) -> RecipeDraft:
    return recipe_draft_model(meal, catalog=CAT).model_validate(payload)


def check(
    meal: MealOption, draft: RecipeDraft, *exclusions: ExclusionTerm
) -> RecipeSteps | Rejection:
    return check_recipe(draft, meal=meal, catalog=CAT, exclusions=frozenset(exclusions))


def test_clean_draft_passes() -> None:
    draft = draft_of(MEAL, valid_payload(MEAL))
    result = check(MEAL, draft, ExclusionTerm.peanuts)
    assert isinstance(result, RecipeSteps)
    assert result.prep_minutes == 10
    assert [s.text for s in result.steps] == [s.text for s in draft.steps]
    assert all(type(s) is RecipeStep for s in result.steps)


def test_excluded_id_rejected() -> None:
    # Bypasses the schema on purpose: the check must not rely on it.
    draft = RecipeDraft.model_construct(
        prep_minutes=5,
        steps=[RecipeStep(text="Mix it.", ingredient_ids=["chicken", "peanut"])],
    )
    result = check(MEAL, draft, ExclusionTerm.peanuts)
    assert isinstance(result, Rejection)
    assert "peanuts" in result.reason
    assert "peanut" in result.reason
    assert result.name == MEAL.draft.name


def test_unknown_id_rejected_by_check() -> None:
    draft = RecipeDraft.model_construct(
        prep_minutes=5, steps=[RecipeStep(text="Mix it.", ingredient_ids=["mystery"])]
    )
    assert isinstance(check(MEAL, draft), Rejection)


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Top with crushed peanuts", ExclusionTerm.peanuts),
        ("Top with CRUSHED PEANUTS", ExclusionTerm.peanuts),
        ("Splash in some soy sauce", ExclusionTerm.soy),
        ("Splash in some shoyu", ExclusionTerm.soy),
    ],
)
def test_excluded_food_in_text_rejected(text: str, term: ExclusionTerm) -> None:
    draft = draft_of(MEAL, payload_with_text(MEAL, text))
    result = check(MEAL, draft, term)
    assert isinstance(result, Rejection)
    assert term.value in result.reason
    assert "step 2" in result.reason


def test_own_ingredient_masked() -> None:
    meal = make_meal(("chicken", "coconut_milk_canned"), name="Coconut chicken")
    draft = draft_of(meal, payload_with_text(meal, "Stir in the coconut milk."))
    assert isinstance(check(meal, draft, ExclusionTerm.milk), RecipeSteps)


def test_masking_does_not_hide_a_separate_excluded_food() -> None:
    meal = make_meal(("chicken", "coconut_milk_canned"), name="Coconut chicken")
    draft = draft_of(meal, payload_with_text(meal, "Stir in the coconut milk and some milk."))
    assert isinstance(check(meal, draft, ExclusionTerm.milk), Rejection)


def test_whole_word_only() -> None:
    # The meal does not use eggplant, so masking cannot be what lets this pass.
    draft = draft_of(MEAL, payload_with_text(MEAL, "Serve with eggplant chips on the side."))
    assert isinstance(check(MEAL, draft, ExclusionTerm.egg), RecipeSteps)


def test_compound_name_of_excluded_food_rejected() -> None:
    draft = draft_of(MEAL, payload_with_text(MEAL, "Stir in a splash of buttermilk."))
    result = check(MEAL, draft, ExclusionTerm.milk)
    assert isinstance(result, Rejection)
    assert "milk" in result.reason
    assert "buttermilk" in result.reason


def test_no_exclusions_no_text_rejection() -> None:
    draft = draft_of(MEAL, payload_with_text(MEAL, "Top with crushed peanuts"))
    assert isinstance(check(MEAL, draft), RecipeSteps)


def test_reason_does_not_echo_unbounded_text() -> None:
    draft = draft_of(MEAL, payload_with_text(MEAL, "Top with peanuts " + "x" * 300))
    result = check(MEAL, draft, ExclusionTerm.peanuts)
    assert isinstance(result, Rejection)
    assert len(result.reason) < 200
