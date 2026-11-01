"""Errors of slate generation and storage (ADR 0019)."""

from collections.abc import Sequence

from mealplanner.planning.models import MealType, Rejection


class ProfileRequiredError(RuntimeError):
    """The user has no saved profile. "No profile" never means "no exclusions" (ADR 0017)."""


class SlateExistsError(RuntimeError):
    """The user already has a slate for that week."""


class SlateGenerationError(RuntimeError):
    """A meal type is still short after the batch call and the bounded top-ups."""

    def __init__(self, meal_type: MealType, rejections: Sequence[Rejection]) -> None:
        self.meal_type = meal_type
        self.rejections: tuple[Rejection, ...] = tuple(rejections)
        reasons = "; ".join(f"{r.name}: {r.reason}" for r in self.rejections[:5])
        super().__init__(f"Could not fill the {meal_type.value} options. {reasons}")
