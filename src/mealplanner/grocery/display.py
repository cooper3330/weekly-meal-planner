"""Display formatting of grocery amounts (ADR 0024): US units, rounded up, never zero."""

import math

from mealplanner.grocery.models import Dimension, GroceryItem, NeedLine
from mealplanner.nutrition.units import G_PER_LB, G_PER_OZ, ML_PER_CUP, ML_PER_TBSP, ML_PER_TSP

_TOL = 1e-9


def _below(amount: float, threshold: float) -> bool:
    """True when ``amount`` is below ``threshold`` by more than float error."""
    return amount < threshold * (1 - _TOL)


def _number(value: float) -> str:
    """Round up to 2 decimals (ignoring float error), trailing zeros removed."""
    rounded = math.ceil(value * 100 * (1 - _TOL)) / 100
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def format_need(line: NeedLine) -> str:
    amount = line.amount
    if line.dimension is Dimension.mass:
        if _below(amount, G_PER_LB):
            return f"{_number(amount / G_PER_OZ)} oz"
        return f"{_number(amount / G_PER_LB)} lb"
    if line.dimension is Dimension.volume:
        if _below(amount, ML_PER_TBSP):
            return f"{_number(amount / ML_PER_TSP)} tsp"
        if _below(amount, ML_PER_CUP / 4):
            return f"{_number(amount / ML_PER_TBSP)} tbsp"
        return f"{_number(amount / ML_PER_CUP)} cup"
    return _number(amount)


def format_buy(item: GroceryItem) -> str | None:
    if item.buy_count is None or item.pack_label is None:
        return None
    return f"Suggested: buy {item.buy_count} x {item.pack_label}"
