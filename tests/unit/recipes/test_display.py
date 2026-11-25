import pytest

from mealplanner.recipes.display import format_amount


@pytest.mark.parametrize(
    ("quantity", "shown"),
    [
        (2.0, "2"),
        (1.50, "1.5"),
        (0.333, "0.33"),
        (0.335, "0.34"),
        (0.0001, "0.01"),
        (0.004, "0.01"),
        (0.005, "0.01"),
        (100.0, "100"),
        (1234567.0, "1234567"),
        (0.0, "0"),
        (12.345, "12.35"),
    ],
)
def test_format_amount(quantity: float, shown: str) -> None:
    assert format_amount(quantity) == shown


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
def test_format_amount_rejects_bad_values(bad: float) -> None:
    with pytest.raises(ValueError):
        format_amount(bad)
