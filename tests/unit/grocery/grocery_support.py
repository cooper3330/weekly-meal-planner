"""Helpers for the grocery builder tests: meals built through ``check_option``."""

from collections.abc import Sequence
from pathlib import Path

from mealplanner.grocery.info import load_grocery_info
from mealplanner.nutrition.catalog import load_catalog
from mealplanner.planning.checks import check_option
from mealplanner.planning.models import MealOption, Rejection
from mealplanner.planning.schema import MealOptionDraft

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CAT = load_catalog(FIXTURES / "catalog" / "planning.csv")
INFO = load_grocery_info(CAT, FIXTURES / "grocery" / "planning.csv")


def make_meal(
    lines: Sequence[tuple[str, float, str]],
    *,
    name: str = "Test meal",
    servings: int = 2,
) -> MealOption:
    """A checked meal: ``lines`` plus "dense" (500 kcal per 100 g) so the kcal check passes."""
    ingredients = [{"ingredient_id": "dense", "quantity": 100 * servings, "unit": "g"}]
    ingredients += [{"ingredient_id": i, "quantity": q, "unit": u} for i, q, u in lines]
    draft = MealOptionDraft.model_validate(
        {
            "name": name,
            "description": "A meal for tests.",
            "cook_minutes": 30,
            "tags": ["quick"],
            "ingredients": ingredients,
        }
    )
    result = check_option(
        draft, catalog=CAT, servings=servings, max_cook_minutes=240, exclusions=frozenset()
    )
    assert not isinstance(result, Rejection), result
    return result
