"""Build a ``Recipe`` from a planned meal and checked steps (ADR 0025). Pure.

Everything but the steps comes from the meal, so the ingredient list and the nutrition cannot
disagree with what the user saw at selection and will see on the grocery list.
"""

from mealplanner.nutrition.catalog import Catalog
from mealplanner.planning.models import MealOption
from mealplanner.recipes.models import Recipe, RecipeIngredient, RecipeSteps


def build_recipe(
    meal: MealOption,
    steps: RecipeSteps,
    *,
    profile_servings: int,
    catalog: Catalog,
) -> Recipe:
    if isinstance(profile_servings, bool) or profile_servings < 1:
        raise ValueError(f"profile_servings must be at least 1, got {profile_servings}")
    total = meal.draft.cook_minutes
    if not 0 <= steps.prep_minutes <= total:
        raise ValueError(f"prep_minutes {steps.prep_minutes} is outside 0 to {total}")
    # Factor first, so equal servings multiply by exactly 1.0 and keep the meal's own number.
    factor = profile_servings / meal.servings
    ingredients = tuple(
        RecipeIngredient(
            ingredient_id=line.ingredient_id,
            name=catalog.get(line.ingredient_id).name,
            quantity=line.quantity * factor,
            unit=line.unit,
        )
        for line in meal.draft.ingredients
    )
    return Recipe(
        option_id=meal.option_id,
        title=meal.draft.name,
        description=meal.draft.description,
        servings=profile_servings,
        total_minutes=total,
        prep_minutes=steps.prep_minutes,
        cook_minutes=total - steps.prep_minutes,
        ingredients=ingredients,
        steps=steps.steps,
        nutrition=meal.nutrition,
    )
