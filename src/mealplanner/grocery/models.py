"""Result types of the grocery list builder (ADR 0024). Holds no nutrition values."""

from dataclasses import dataclass
from enum import Enum

from mealplanner.grocery.aisles import Aisle


class Dimension(Enum):
    """The order here is the order need lines are listed in.

    Not a StrEnum: ``count`` would clash with ``str.count``.
    """

    mass = "mass"
    volume = "volume"
    count = "count"


@dataclass(frozen=True)
class NeedLine:
    """An unrounded amount in grams (mass), millilitres (volume) or pieces (count)."""

    dimension: Dimension
    amount: float


@dataclass(frozen=True)
class GroceryItem:
    ingredient_id: str
    name: str
    aisle: Aisle
    needs: tuple[NeedLine, ...]
    buy_count: int | None
    pack_label: str | None
    meals: tuple[str, ...]


@dataclass(frozen=True)
class AisleSection:
    aisle: Aisle
    items: tuple[GroceryItem, ...]


@dataclass(frozen=True)
class GroceryList:
    sections: tuple[AisleSection, ...]
