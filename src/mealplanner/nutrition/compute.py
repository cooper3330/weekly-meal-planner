"""Per-serving nutrition computed from the catalog. Unrounded until display."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.units import Unit, to_grams


@dataclass(frozen=True)
class Nutrition:
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


@dataclass(frozen=True)
class DisplayNutrition:
    kcal: int
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal


def compute_nutrition(
    lines: Iterable[tuple[str, float, Unit]], catalog: Catalog, servings: int
) -> Nutrition:
    """Per-serving totals: sum of grams / 100 * per_100g, divided by servings."""
    if servings < 1:
        raise ValueError(f"servings must be at least 1, got {servings}")
    kcal = protein = carbs = fat = 0.0
    for ingredient_id, quantity, unit in lines:
        row = catalog.get(ingredient_id)  # KeyError for an unknown id
        grams = to_grams(quantity, unit, row)
        kcal += grams / 100 * row.kcal_per_100g
        protein += grams / 100 * row.protein_g_per_100g
        carbs += grams / 100 * row.carbs_g_per_100g
        fat += grams / 100 * row.fat_g_per_100g
    return Nutrition(kcal / servings, protein / servings, carbs / servings, fat / servings)


def _half_up(value: float, places: str) -> Decimal:
    return Decimal(repr(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def display_nutrition(n: Nutrition) -> DisplayNutrition:
    """Round for display: kcal to an int, macros to one decimal, half-up."""
    return DisplayNutrition(
        kcal=int(_half_up(n.kcal, "1")),
        protein_g=_half_up(n.protein_g, "0.1"),
        carbs_g=_half_up(n.carbs_g, "0.1"),
        fat_g=_half_up(n.fat_g, "0.1"),
    )
