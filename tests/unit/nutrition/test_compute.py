import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from mealplanner.nutrition.catalog import load_catalog
from mealplanner.nutrition.compute import Nutrition, compute_nutrition, display_nutrition
from mealplanner.nutrition.units import Unit

CAT = load_catalog(Path(__file__).resolve().parents[2] / "fixtures" / "catalog" / "valid.csv")


def test_single_ingredient_exact() -> None:
    # 200 g chicken = 2 x (165 kcal, 31 g protein); / 2 servings = 165 kcal, 31 g.
    n = compute_nutrition([("chicken", 200, Unit.g)], CAT, 2)
    assert n.kcal == 165.0
    assert n.protein_g == 31.0
    assert n.carbs_g == 0.0
    assert n.fat_g == pytest.approx(3.6, rel=1e-12)


def test_mixed_units_exact() -> None:
    # 150 g rice: 195 kcal, 4.5 p, 42 c, 0.45 f.
    # 2 tbsp olive oil = 2 * 14.78676478125 * 0.9 g = 26.616176606... g: kcal 9x/ g, fat 1x/ g.
    # 2 each egg = 100 g: 150 kcal, 12 p, 1 c, 10 f. All divided by 2 servings.
    oil_g = 2 * 14.78676478125 * 0.9
    n = compute_nutrition(
        [("rice", 150, Unit.g), ("olive_oil", 2, Unit.tbsp), ("egg", 2, Unit.each)], CAT, 2
    )
    assert n.kcal == pytest.approx((195 + oil_g * 9 + 150) / 2, rel=1e-12)
    assert n.protein_g == pytest.approx((4.5 + 12) / 2, rel=1e-12)
    assert n.carbs_g == pytest.approx((42 + 1) / 2, rel=1e-12)
    assert n.fat_g == pytest.approx((0.45 + oil_g + 10) / 2, rel=1e-12)


def test_duplicate_lines_sum() -> None:
    twice = compute_nutrition([("rice", 100, Unit.g), ("rice", 50, Unit.g)], CAT, 1)
    once = compute_nutrition([("rice", 150, Unit.g)], CAT, 1)
    assert twice.kcal == pytest.approx(once.kcal, rel=1e-12)
    assert twice.fat_g == pytest.approx(once.fat_g, rel=1e-12)


@pytest.mark.parametrize("servings", [1, 2, 12])
def test_servings_divides(servings: int) -> None:
    n = compute_nutrition([("rice", 1200, Unit.g)], CAT, servings)  # 1560 kcal total
    assert n.kcal == pytest.approx(1560 / servings, rel=1e-12)


@pytest.mark.parametrize("servings", [0, -1])
def test_servings_zero_rejected(servings: int) -> None:
    with pytest.raises(ValueError):
        compute_nutrition([("rice", 100, Unit.g)], CAT, servings)


def test_unknown_id_raises() -> None:
    with pytest.raises(KeyError):
        compute_nutrition([("pasta", 100, Unit.g)], CAT, 1)


def test_values_stay_unrounded() -> None:
    # 100 g rice = 130 kcal; / 3 servings = 43.333... kcal.
    n = compute_nutrition([("rice", 100, Unit.g)], CAT, 3)
    assert n.kcal == pytest.approx(43.333333333333336, rel=1e-15)
    assert n.kcal != round(n.kcal, 1)


@pytest.mark.parametrize(
    ("kcal", "expected"),
    [(512.5, 513), (512.4999, 512), (0.0, 0), (0.5, 1)],
)
def test_display_kcal_rounding(kcal: float, expected: int) -> None:
    d = display_nutrition(Nutrition(kcal, 0, 0, 0))
    assert d.kcal == expected
    assert isinstance(d.kcal, int)


@pytest.mark.parametrize(
    ("grams", "expected"),
    [(10.25, "10.3"), (10.249, "10.2"), (0.0, "0.0"), (33.333333333, "33.3"), (0.05, "0.1")],
)
def test_display_macro_rounding(grams: float, expected: str) -> None:
    d = display_nutrition(Nutrition(0, grams, grams, grams))
    for value in (d.protein_g, d.carbs_g, d.fat_g):
        assert isinstance(value, Decimal)
        assert value == Decimal(expected)
        assert str(value) == expected


def test_nutrition_has_no_network() -> None:
    # The autouse offline guard and the absent API key cover this; compute just works.
    assert compute_nutrition([("egg", 1, Unit.each)], CAT, 1).kcal == 75.0


def test_nutrition_package_imports() -> None:
    src = str(Path(__file__).resolve().parents[3] / "src")
    code = (
        f"import sys; sys.path.insert(0, {src!r}); import mealplanner.nutrition.compute;"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('fastapi', 'jinja2', 'anthropic') or m.startswith('mealplanner.planning')];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
