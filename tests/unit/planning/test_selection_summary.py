from slate_support import CAT, option

from mealplanner.nutrition.compute import Nutrition, compute_nutrition, display_nutrition
from mealplanner.planning.models import MealOption
from mealplanner.planning.schema import MealOptionDraft
from mealplanner.planning.selection import summarize

ZERO = Nutrition(0.0, 0.0, 0.0, 0.0)


def meal(index: int, nutrition: Nutrition | None = None, servings: int = 2) -> MealOption:
    draft = MealOptionDraft.model_validate(option(index))
    n = nutrition or compute_nutrition(
        [(i.ingredient_id, i.quantity, i.unit) for i in draft.ingredients], CAT, servings
    )
    return MealOption(draft=draft, servings=servings, nutrition=n)


def test_summary_empty() -> None:
    s = summarize([])
    assert s.count == 0
    assert s.total == ZERO
    assert s.average == ZERO


def test_summary_sum_and_mean() -> None:
    meals = [meal(1), meal(2), meal(3)]
    s = summarize(meals)
    assert s.count == 3
    for field in ("kcal", "protein_g", "carbs_g", "fat_g"):
        expected = sum(getattr(m.nutrition, field) for m in meals)
        assert getattr(s.total, field) == expected
        assert getattr(s.average, field) == expected / 3


def test_summary_rounds_once() -> None:
    meals = [meal(1, Nutrition(100.4, 10.04, 0.0, 0.0)), meal(2, Nutrition(100.4, 10.04, 0.0, 0.0))]
    assert display_nutrition(meals[0].nutrition).kcal == 100
    shown = display_nutrition(summarize(meals).total)
    assert shown.kcal == 201
    assert str(shown.protein_g) == "20.1"


def test_summary_single_option() -> None:
    m = meal(5)
    s = summarize([m])
    assert s.count == 1
    assert s.total == m.nutrition
    assert s.average == m.nutrition
