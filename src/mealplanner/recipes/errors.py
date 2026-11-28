"""Errors of recipe generation (ADR 0025)."""

from collections.abc import Sequence


class RecipeGenerationError(RuntimeError):
    """Every draft for a meal was rejected by the domain check. Nothing is stored or shown."""

    def __init__(self, meal_name: str, reasons: Sequence[str]) -> None:
        self.meal_name = meal_name
        self.reasons: tuple[str, ...] = tuple(reasons)
        super().__init__(f"Could not write a recipe for {meal_name}. " + "; ".join(self.reasons))


class RecipeNotFoundError(LookupError):
    """The meal is not in this user's current confirmed plan: unpicked, replaced, another
    user's, or the week is open (ADR 0025)."""


class WeekNotConfirmedError(RuntimeError):
    """The week has no current confirmation, so no recipe is built (ADR 0021)."""


class MealNoLongerFitsError(RuntimeError):
    """A planned meal fails the profile checks now, so every recipe is withheld (ADR 0021)."""
