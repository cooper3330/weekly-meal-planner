"""Result types of the recipe checks and builder (ADR 0025)."""

from dataclasses import dataclass

from mealplanner.nutrition.compute import Nutrition
from mealplanner.nutrition.units import Unit
from mealplanner.recipes.schema import RecipeStep


@dataclass(frozen=True)
class RecipeIngredient:
    ingredient_id: str
    name: str  # the catalog name
    quantity: float  # unrounded; ``format_amount`` rounds for display
    unit: Unit


@dataclass(frozen=True)
class RecipeSteps:
    """A draft that passed ``check_recipe``. Only ``check_recipe`` builds one."""

    prep_minutes: int
    steps: tuple[RecipeStep, ...]


@dataclass(frozen=True)
class Recipe:
    option_id: int | None
    title: str
    description: str
    servings: int
    total_minutes: int
    prep_minutes: int
    cook_minutes: int
    ingredients: tuple[RecipeIngredient, ...]
    steps: tuple[RecipeStep, ...]
    nutrition: Nutrition
