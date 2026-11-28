"""Result types of the recipe checks and builder (ADR 0025)."""

from dataclasses import dataclass

from mealplanner.nutrition.compute import Nutrition
from mealplanner.nutrition.units import Unit
from mealplanner.planning.models import UnavailableOption
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


# --- Results of the recipe service (ADR 0025) ---------------------------------------------


@dataclass(frozen=True)
class GenerationFailure:
    option_id: int
    meal_name: str
    message: str
    # The error class, so a page can choose its own plain wording: ``message`` may carry model
    # text and is never shown.
    cause: type[Exception] = Exception


@dataclass(frozen=True)
class GenerationReport:
    """Which meals got a recipe in this call, and which failed and why. Meals that already had
    a recipe that passes are in neither list."""

    succeeded: tuple[int, ...] = ()
    failed: tuple[GenerationFailure, ...] = ()


@dataclass(frozen=True)
class Missing:
    """A planned meal with no stored recipe."""

    option_id: int
    name: str


@dataclass(frozen=True)
class StepsNoLongerFit:
    """A planned meal whose stored steps fail the checks now. The steps are not returned."""

    option_id: int
    name: str
    reason: str


@dataclass(frozen=True)
class WeekOpen:
    """No current confirmation: no recipe content is shown."""


@dataclass(frozen=True)
class MealNoLongerFits:
    """A planned meal fails the profile checks, so every recipe is withheld (ADR 0021)."""

    unavailable: tuple[UnavailableOption, ...]


@dataclass(frozen=True)
class NoMealsPlanned:
    """The week is confirmed with no meals."""


RecipeItem = Recipe | Missing | StepsNoLongerFit


@dataclass(frozen=True)
class RecipesReady:
    """One item per planned meal, in plan order."""

    items: tuple[RecipeItem, ...]
