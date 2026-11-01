"""Result types of the domain checks (ADR 0018)."""

from dataclasses import dataclass
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
