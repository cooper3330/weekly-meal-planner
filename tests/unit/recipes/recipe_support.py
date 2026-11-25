"""Helpers for the recipe tests: meals built through ``check_option``, and step payloads."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mealplanner.nutrition.catalog import load_catalog
from mealplanner.planning.checks import check_option
from mealplanner.planning.models import MealOption, Rejection
from mealplanner.planning.schema import MealOptionDraft

ROOT = Path(__file__).resolve().parents[3]
CAT = load_catalog(ROOT / "tests" / "fixtures" / "catalog" / "recipes.csv")


def make_meal(
    ids: Sequence[str] = ("chicken", "rice"),
    *,
    servings: int = 2,
    cook_minutes: int = 30,
    name: str = "Chicken and rice",
    unit: str = "g",
    quantity: float = 100,
    option_id: int | None = None,
) -> MealOption:
    """A checked meal: 200 g of "dense" (500 kcal per serving for 2), plus each id."""
    lines: list[dict[str, Any]] = [{"ingredient_id": "dense", "quantity": 200, "unit": "g"}]
    lines += [{"ingredient_id": i, "quantity": quantity, "unit": unit} for i in ids]
    draft = MealOptionDraft.model_validate(
        {
            "name": name,
            "description": "A meal for tests.",
            "cook_minutes": cook_minutes,
            "tags": ["quick"],
            "ingredients": lines,
        }
    )
    result = check_option(
        draft, catalog=CAT, servings=servings, max_cook_minutes=240, exclusions=frozenset()
    )
    assert not isinstance(result, Rejection), result
    if option_id is None:
        return result
    return MealOption(
        draft=result.draft,
        servings=result.servings,
        nutrition=result.nutrition,
        option_id=option_id,
    )


def meal_ids(meal: MealOption) -> list[str]:
    return [line.ingredient_id for line in meal.draft.ingredients]


def valid_payload(meal: MealOption, *, prep: int = 10) -> dict[str, Any]:
    """Step JSON that passes every rule: every id used, no amounts."""
    return {
        "prep_minutes": prep,
        "steps": [
            {"text": "Get everything ready.", "ingredient_ids": meal_ids(meal)},
            {"text": "Cook it all together, then serve.", "ingredient_ids": []},
        ],
    }


def payload_with_text(meal: MealOption, text: str, *, ids: Sequence[str] | None = None) -> Any:
    """A valid payload whose second step has ``text`` (and optionally these ids)."""
    payload = valid_payload(meal)
    payload["steps"][1] = {"text": text, "ingredient_ids": list(ids or [])}
    return payload
