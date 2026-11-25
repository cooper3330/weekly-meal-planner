from mealplanner.grocery.aisles import Aisle
from mealplanner.grocery.display import format_buy, format_need
from mealplanner.grocery.models import Dimension, GroceryItem, NeedLine
from mealplanner.nutrition.units import G_PER_LB, ML_PER_CUP, ML_PER_TBSP, ML_PER_TSP


def mass(g: float) -> str:
    return format_need(NeedLine(Dimension.mass, g))


def vol(ml: float) -> str:
    return format_need(NeedLine(Dimension.volume, ml))


def count(n: float) -> str:
    return format_need(NeedLine(Dimension.count, n))


def test_mass_units() -> None:
    assert mass(100) == "3.53 oz"
    assert mass(453.59237) == "1 lb"
    assert mass(1000) == "2.21 lb"


def test_mass_threshold_tolerates_float_error() -> None:
    assert mass(G_PER_LB * (1 - 1e-12)) == "1 lb"
    assert mass(G_PER_LB * 0.99) == "15.84 oz"


def test_volume_units() -> None:
    assert vol(4.92892159375) == "1 tsp"
    assert vol(14.78676478125) == "1 tbsp"
    assert vol(ML_PER_CUP / 4) == "0.25 cup"
    assert vol(59.14) == "4 tbsp"
    assert vol(3 * ML_PER_TSP) == "1 tbsp"
    assert vol(2 * ML_PER_TBSP) == "2 tbsp"


def test_count() -> None:
    assert count(2) == "2"
    assert count(0.333) == "0.34"


def test_tiny_never_zero() -> None:
    assert mass(0.0001) == "0.01 oz"


def test_round_up_not_half() -> None:
    assert mass(1.001 * 28.349523125) == "1.01 oz"


def test_trailing_zeros_removed() -> None:
    assert count(1.5) == "1.5"
    assert count(2.0) == "2"
    assert count(1.499999) == "1.5"


def _item(buy_count: int | None, label: str | None) -> GroceryItem:
    return GroceryItem("x", "X", Aisle.produce, (), buy_count, label, ("Meal",))


def test_format_buy() -> None:
    assert format_buy(_item(2, "16 oz bag")) == "Suggested: buy 2 x 16 oz bag"
    assert format_buy(_item(None, None)) is None
