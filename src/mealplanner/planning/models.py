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
