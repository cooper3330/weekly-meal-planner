"""Unit conversion to grams. The unit set is closed (ADR 0018)."""

from enum import StrEnum

from mealplanner.nutrition.catalog import CatalogRow


class Unit(StrEnum):
    g = "g"
    kg = "kg"
    ml = "ml"
    l = "l"  # noqa: E741
    tsp = "tsp"
    tbsp = "tbsp"
    cup = "cup"
    oz = "oz"
    lb = "lb"
    each = "each"


class NoConversionError(ValueError):
    """The ingredient has no density or piece weight for this unit."""


G_PER_KG = 1000.0
G_PER_OZ = 28.349523125
G_PER_LB = 453.59237
ML_PER_L = 1000.0
ML_PER_TSP = 4.92892159375
ML_PER_TBSP = 14.78676478125
ML_PER_CUP = 236.5882365

_MASS_G = {Unit.g: 1.0, Unit.kg: G_PER_KG, Unit.oz: G_PER_OZ, Unit.lb: G_PER_LB}
_VOLUME_ML = {
    Unit.ml: 1.0,
    Unit.l: ML_PER_L,
    Unit.tsp: ML_PER_TSP,
    Unit.tbsp: ML_PER_TBSP,
    Unit.cup: ML_PER_CUP,
}


def can_convert(unit: Unit, row: CatalogRow) -> bool:
    if unit in _MASS_G:
        return True
    if unit is Unit.each:
        return row.g_per_each is not None
    return row.g_per_ml is not None


def to_grams(quantity: float, unit: Unit, row: CatalogRow) -> float:
    if unit in _MASS_G:
        return quantity * _MASS_G[unit]
    if unit is Unit.each:
        if row.g_per_each is None:
            raise NoConversionError(f"{row.id} has no g_per_each, cannot convert 'each'")
        return quantity * row.g_per_each
    if row.g_per_ml is None:
        raise NoConversionError(f"{row.id} has no g_per_ml, cannot convert {unit.value!r}")
    return quantity * _VOLUME_ML[unit] * row.g_per_ml
