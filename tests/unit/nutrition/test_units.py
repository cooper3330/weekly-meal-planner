from pathlib import Path

import pytest

from mealplanner.nutrition.catalog import load_catalog
from mealplanner.nutrition.units import NoConversionError, Unit, can_convert, to_grams

CAT = load_catalog(Path(__file__).resolve().parents[2] / "fixtures" / "catalog" / "valid.csv")
RICE = CAT.get("rice")  # no density, no piece weight
MILK = CAT.get("milk")  # g_per_ml 1.03
EGG = CAT.get("egg")  # g_per_each 50


def test_mass_units() -> None:
    assert to_grams(1, Unit.g, RICE) == 1
    assert to_grams(1, Unit.kg, RICE) == 1000
    assert to_grams(16, Unit.oz, RICE) == pytest.approx(16 * 28.349523125, rel=1e-12)
    assert to_grams(1, Unit.lb, RICE) == pytest.approx(453.59237, rel=1e-12)
    assert to_grams(16, Unit.oz, RICE) == pytest.approx(453.59237, rel=1e-9)


def test_volume_units_need_density() -> None:
    assert to_grams(1, Unit.cup, MILK) == pytest.approx(236.5882365 * 1.03, rel=1e-12)
    assert to_grams(1, Unit.tsp, MILK) == pytest.approx(4.92892159375 * 1.03, rel=1e-12)
    assert to_grams(1, Unit.tbsp, MILK) == pytest.approx(14.78676478125 * 1.03, rel=1e-12)
    assert to_grams(1, Unit.ml, MILK) == pytest.approx(1.03, rel=1e-12)
    assert to_grams(1, Unit.l, MILK) == pytest.approx(1030, rel=1e-12)
    for unit in (Unit.ml, Unit.l, Unit.tsp, Unit.tbsp, Unit.cup):
        assert can_convert(unit, MILK)
        assert not can_convert(unit, RICE)
        with pytest.raises(NoConversionError):
            to_grams(1, unit, RICE)


def test_each_needs_weight() -> None:
    assert to_grams(3, Unit.each, EGG) == 150
    assert can_convert(Unit.each, EGG)
    assert not can_convert(Unit.each, RICE)
    with pytest.raises(NoConversionError):
        to_grams(1, Unit.each, RICE)


def test_mass_units_always_convertible() -> None:
    for unit in (Unit.g, Unit.kg, Unit.oz, Unit.lb):
        assert can_convert(unit, RICE)


def test_unit_enum_closed() -> None:
    assert {u.value for u in Unit} == {
        "g", "kg", "ml", "l", "tsp", "tbsp", "cup", "oz", "lb", "each"
    }  # fmt: skip
    with pytest.raises(ValueError):
        Unit("pinch")
    with pytest.raises(ValueError):
        Unit("G")
