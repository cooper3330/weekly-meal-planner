"""Display helpers for recipes (ADR 0025)."""

import math
from decimal import ROUND_HALF_UP, Decimal

_MIN_SHOWN = Decimal("0.01")


def format_amount(quantity: float) -> str:
    """Half-up to 2 decimal places, trailing zeros removed; never ``0`` for a positive amount."""
    if not math.isfinite(quantity) or quantity < 0:
        raise ValueError(f"quantity must be finite and not negative, got {quantity!r}")
    rounded = Decimal(repr(float(quantity))).quantize(_MIN_SHOWN, rounding=ROUND_HALF_UP)
    if quantity > 0 and rounded < _MIN_SHOWN:
        rounded = _MIN_SHOWN
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
