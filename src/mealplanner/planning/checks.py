"""Domain checks on a schema-valid draft (ADR 0018). Pure functions: no model, no database."""

import math
import re
import unicodedata
from collections.abc import Iterable

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.compute import compute_nutrition
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.nutrition.units import NoConversionError, to_grams
from mealplanner.planning.models import MealOption, RecentMeal, Rejection
from mealplanner.planning.schema import MealOptionDraft

MAX_GRAMS_PER_SERVING = 1000.0
MIN_KCAL = 150.0
MAX_KCAL = 2500.0

_NON_ALNUM = re.compile(r"[\W_]+")


def check_option(
    draft: MealOptionDraft,
    *,
    catalog: Catalog,
    servings: int,
    max_cook_minutes: int,
    exclusions: Iterable[ExclusionTerm],
) -> MealOption | Rejection:
    """Turn a draft into a displayable option, or say why it cannot be shown."""
    excluded = frozenset(exclusions)

    def reject(reason: str) -> Rejection:
        return Rejection(name=draft.name, reason=reason)

    # 1. Exclusions: exact set intersection over closed terms (ADR 0016).
    for line in draft.ingredients:
        if line.ingredient_id not in catalog:
            return reject(f"unknown ingredient id {line.ingredient_id!r}")
        row = catalog.get(line.ingredient_id)
        hits = (row.allergens | row.diet_tags) & excluded
        if hits:
            term = sorted(hits, key=lambda t: t.value)[0]
            return reject(f"contains excluded {term.value} (ingredient {line.ingredient_id})")

    # 2. Cook time.
    if draft.cook_minutes > max_cook_minutes:
        return reject(f"cook time {draft.cook_minutes} min is over the maximum {max_cook_minutes}")

    # 3. Quantity sanity: at most 1000 g of any line per serving.
    for line in draft.ingredients:
        try:
            grams = to_grams(line.quantity, line.unit, catalog.get(line.ingredient_id))
        except NoConversionError as e:
            return reject(str(e))
        if grams / servings > MAX_GRAMS_PER_SERVING:
            return reject(
                f"{line.ingredient_id} is {grams / servings:.1f} g per serving, "
                f"over {MAX_GRAMS_PER_SERVING:.0f} g"
            )

    # 4. Nutrition, computed in code.
    nutrition = compute_nutrition(
        [(line.ingredient_id, line.quantity, line.unit) for line in draft.ingredients],
        catalog,
        servings,
    )
    values = (nutrition.kcal, nutrition.protein_g, nutrition.carbs_g, nutrition.fat_g)
    if not all(math.isfinite(v) and v >= 0 for v in values):
        return reject("computed nutrition is not finite and non-negative")
    if not MIN_KCAL <= nutrition.kcal <= MAX_KCAL:
        return reject(
            f"{nutrition.kcal:.1f} kcal per serving is outside {MIN_KCAL:.0f} to {MAX_KCAL:.0f}"
        )
    return MealOption(draft=draft, servings=servings, nutrition=nutrition)


def meal_key(name: str) -> str:
    """NFKC, casefold, runs of non-alphanumerics become one space, trimmed."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return _NON_ALNUM.sub(" ", folded).strip()


def _ids(meal: MealOptionDraft | RecentMeal) -> frozenset[str]:
    if isinstance(meal, RecentMeal):
        return meal.ingredient_ids
    return frozenset(line.ingredient_id for line in meal.ingredients)


def same_meal(a: MealOptionDraft | RecentMeal, b: MealOptionDraft | RecentMeal) -> bool:
    """The same meal: equal name keys, or equal sets of ingredient ids."""
    return meal_key(a.name) == meal_key(b.name) or _ids(a) == _ids(b)
