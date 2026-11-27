"""Result types of the domain checks (ADR 0018)."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from mealplanner.nutrition.compute import Nutrition
from mealplanner.planning.schema import MealOptionDraft


class MealType(StrEnum):
    lunch = "lunch"
    dinner = "dinner"


@dataclass(frozen=True)
class MealOption:
    """A displayable option. Only ``check_option`` builds one, so nutrition is always computed."""

    draft: MealOptionDraft
    servings: int
    nutrition: Nutrition
    # Set when the option is read back from a stored slate.
    option_id: int | None = None


@dataclass(frozen=True)
class Rejection:
    """A draft that failed a domain check. Never shown to the user."""

    name: str
    reason: str


@dataclass(frozen=True)
class RecentMeal:
    """A stored earlier meal, reduced to what the same-meal rule needs."""

    name: str
    ingredient_ids: frozenset[str]


@dataclass(frozen=True)
class UnavailableOption:
    """A stored option that no longer passes the checks, for example after a new exclusion."""

    option_id: int
    reason: str


Slot = MealOption | UnavailableOption


@dataclass(frozen=True)
class SlateView:
    """A slate as it may be shown: every slot rebuilt through ``check_option`` on read."""

    slate_id: int
    week_start: date
    profile_version: int
    options: dict[MealType, tuple[Slot, ...]]


@dataclass(frozen=True)
class NutritionSummary:
    """Sum and mean of the unrounded per-serving nutrition of the chosen meals (ADR 0021)."""

    count: int
    total: Nutrition
    average: Nutrition


@dataclass(frozen=True)
class SelectionItem:
    slot: Slot
    picked: bool


@dataclass(frozen=True)
class SelectionView:
    """A slate with the draft picks, the limits and the summary of the available picks."""

    slate_id: int
    week_start: date
    lunch_limit: int
    dinner_limit: int
    items: dict[MealType, tuple[SelectionItem, ...]]
    pick_counts: dict[MealType, int]
    summary: NutritionSummary
    confirmed: bool = False
    confirmation_id: int | None = None
    # Picked meals of the current plan that now fail the checks (only while confirmed).
    confirmed_unavailable: tuple[UnavailableOption, ...] = ()

    def limit(self, meal_type: MealType) -> int:
        return self.lunch_limit if meal_type is MealType.lunch else self.dinner_limit


@dataclass(frozen=True)
class ConfirmedPlan:
    """A confirmed week (ADR 0021). ``meals`` may be empty: a week with no meals planned."""

    confirmation_id: int
    week_start: date
    meals: list[MealOption]
    unavailable: list[UnavailableOption]
