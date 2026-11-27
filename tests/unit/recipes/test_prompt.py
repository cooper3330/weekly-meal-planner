from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import Rejection
from mealplanner.recipes.prompt import build_recipe_request, build_recipe_system
from recipes.recipe_support import CAT, make_meal

MEAL = make_meal(("chicken", "olive_oil"), servings=3, quantity=12.5, cook_minutes=35)


def request(*rejected: Rejection, exclusions: frozenset[ExclusionTerm] = frozenset()) -> str:
    return build_recipe_request(MEAL, catalog=CAT, exclusions=exclusions, rejected=rejected)


def test_request_lists_meal_lines() -> None:
    text = request()
    assert "dense | Test dense fuel (500 kcal per 100 g) | 200 g" in text
    assert "chicken | Chicken breast | 12.5 g" in text
    assert "olive_oil | Olive oil | 12.5 g" in text
    assert "Chicken and rice" in text
    assert "35 minutes" in text
    assert "3 servings" in text


def test_large_and_small_quantities_are_not_exponent_form() -> None:
    meal = make_meal(("chicken",), quantity=0.00001)
    text = build_recipe_request(meal, catalog=CAT, exclusions=frozenset(), rejected=[])
    assert "e-" not in text
    assert "0.00001 g" in text


def test_no_preferences_or_skill() -> None:
    # The builders take no profile, so nothing of it can be sent. This pins that down.
    texts = (build_recipe_system(), request(exclusions=frozenset({ExclusionTerm.soy})))
    for text in texts:
        assert "<user_preferences>" not in text
        assert "beginner" not in text
        assert "advanced" not in text
        assert "intermediate" not in text


def test_system_forbids_amounts_and_nutrition() -> None:
    text = build_recipe_system()
    assert "Never state an amount" in text
    assert "nutrition" in text
    assert "{{" not in text


def test_rejections_in_request() -> None:
    first = request()
    assert "(none)" in first
    text = request(
        Rejection(name="Chicken and rice", reason="step 2 names 'peanuts' (peanut)"),
        Rejection(name="Chicken and rice", reason="step 1 names 'shoyu' (soy_sauce)"),
    )
    assert "step 2 names 'peanuts' (peanut)" in text
    assert "step 1 names 'shoyu' (soy_sauce)" in text


def test_exclusion_labels_listed() -> None:
    text = request(exclusions=frozenset({ExclusionTerm.soy, ExclusionTerm.peanuts}))
    assert "Peanuts, Soybeans" in text


def test_text_in_the_meal_cannot_fill_another_placeholder() -> None:
    meal = make_meal(name="Odd {{SERVINGS}} meal")
    text = build_recipe_request(meal, catalog=CAT, exclusions=frozenset(), rejected=[])
    assert "Odd {{SERVINGS}} meal" in text
