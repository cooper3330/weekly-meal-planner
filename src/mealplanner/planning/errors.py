"""Errors of slate generation and storage (ADR 0019)."""

from collections.abc import Sequence

from mealplanner.planning.models import MealType, Rejection


class ProfileRequiredError(RuntimeError):
    """The user has no saved profile. "No profile" never means "no exclusions" (ADR 0017)."""


class OptionNotFoundError(LookupError):
    """No active option with that id belongs to the user (missing, foreign or already replaced)."""


class OptionUnavailableError(RuntimeError):
    """The option fails the checks now (for example a new exclusion), so it cannot be picked."""


class SelectionLimitError(ValueError):
    """A pick would pass the limit, or a limit is invalid or below the current number of picks."""


class SelectionLockedError(RuntimeError):
    """The week is confirmed, so picks, limits and a second confirmation are refused (ADR 0021)."""


class OptionPickedError(RuntimeError):
    """The option is picked, so it cannot be replaced until it is unpicked (ADR 0021)."""


class SelectionNotConfirmedError(RuntimeError):
    """The week has no current confirmation, so there is nothing to reopen."""


class UnavailablePickError(RuntimeError):
    """A picked option no longer passes the checks, so the week cannot be confirmed (P9)."""


class SlateNotFoundError(LookupError):
    """The user has no slate for that week."""


class SlateExistsError(RuntimeError):
    """The user already has a slate for that week."""


class SlateGenerationError(RuntimeError):
    """A meal type is still short after the batch call and the bounded top-ups."""

    def __init__(self, meal_type: MealType, rejections: Sequence[Rejection]) -> None:
        self.meal_type = meal_type
        self.rejections: tuple[Rejection, ...] = tuple(rejections)
        reasons = "; ".join(f"{r.name}: {r.reason}" for r in self.rejections[:5])
        super().__init__(f"Could not fill the {meal_type.value} options. {reasons}")
