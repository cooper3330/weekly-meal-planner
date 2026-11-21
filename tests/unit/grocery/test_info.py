"""Loader rules for the grocery file (ADR 0023)."""

import math
from pathlib import Path

import pytest

from mealplanner.grocery.aisles import Aisle
from mealplanner.grocery.info import GroceryInfoError, load_grocery_info
from mealplanner.nutrition.catalog import load_catalog
from mealplanner.nutrition.units import Unit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CAT = load_catalog(FIXTURES / "catalog" / "valid.csv")
HEADER = "id,aisle,pack_quantity,pack_unit,pack_label"
ROWS = {
    "chicken": "chicken,meat_seafood,1,lb,1 lb pack",
    "rice": "rice,pasta_rice_grains,2,lb,2 lb bag",
    "milk": "milk,dairy_eggs,1893,ml,1/2 gallon carton",
    "egg": "egg,dairy_eggs,12,each,1 dozen",
    "olive_oil": "olive_oil,oils_condiments_sauces,500,ml,16.9 fl oz bottle",
    "soy_sauce": "soy_sauce,international,296,ml,10 fl oz bottle",
}


def write(tmp_path: Path, *, header: str = HEADER, **overrides: str | None) -> Path:
    """A valid file for the fixture catalog; ``overrides`` replace a row (None drops it)."""
    rows = {**ROWS, **overrides}
    lines = [header, *(r for r in rows.values() if r is not None)]
    path = tmp_path / "grocery.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_loads_valid_file() -> None:
    info = load_grocery_info(CAT, FIXTURES / "grocery" / "valid.csv")
    egg = info.get("egg")
    assert egg.aisle is Aisle.dairy_eggs
    assert egg.pack_quantity == 12.0
    assert egg.pack_unit is Unit.each
    assert egg.pack_label == "1 dozen"
    assert info.get("milk").pack_unit is Unit.ml
    assert info.ids() == CAT.ids()


def test_loads_planning_fixture_with_not_purchased() -> None:
    cat = load_catalog(FIXTURES / "catalog" / "planning.csv")
    info = load_grocery_info(cat, FIXTURES / "grocery" / "planning.csv")
    fuel = info.get("fuel")
    assert fuel.aisle is Aisle.not_purchased
    assert (fuel.pack_quantity, fuel.pack_unit, fuel.pack_label) == (None, None, None)


def test_get_unknown_id_raises_keyerror(tmp_path: Path) -> None:
    info = load_grocery_info(CAT, write(tmp_path))
    with pytest.raises(KeyError):
        info.get("nope")


def test_missing_catalog_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(GroceryInfoError, match="'olive_oil'"):
        load_grocery_info(CAT, write(tmp_path, olive_oil=None))


def test_unknown_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(GroceryInfoError, match="'ghost'"):
        load_grocery_info(CAT, write(tmp_path, ghost="ghost,produce,1,lb,1 lb"))


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(GroceryInfoError, match="duplicate id 'rice'"):
        load_grocery_info(CAT, write(tmp_path, rice2=ROWS["rice"]))


def test_unknown_aisle_rejected(tmp_path: Path) -> None:
    with pytest.raises(GroceryInfoError, match="column aisle"):
        load_grocery_info(CAT, write(tmp_path, rice="rice,deli,2,lb,2 lb bag"))


@pytest.mark.parametrize(
    "header",
    [
        "id,aisle,pack_quantity,pack_unit",
        "id,aisle,pack_quantity,pack_unit,pack_label,extra",
        "id,aisle,pack_unit,pack_quantity,pack_label",
        "",
    ],
)
def test_bad_header_rejected(tmp_path: Path, header: str) -> None:
    with pytest.raises(GroceryInfoError, match="line 1, column header"):
        load_grocery_info(CAT, write(tmp_path, header=header))


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf", "-inf", "abc"])
def test_pack_quantity_invalid(tmp_path: Path, bad: str) -> None:
    row = f"rice,pasta_rice_grains,{bad},lb,2 lb bag"
    with pytest.raises(GroceryInfoError, match="column pack_quantity"):
        load_grocery_info(CAT, write(tmp_path, rice=row))
    assert not math.isfinite(float("nan"))


@pytest.mark.parametrize("unit", ["pinch", "bunch", "LB"])
def test_pack_unit_unknown(tmp_path: Path, unit: str) -> None:
    row = f"rice,pasta_rice_grains,2,{unit},2 lb bag"
    with pytest.raises(GroceryInfoError, match="column pack_unit"):
        load_grocery_info(CAT, write(tmp_path, rice=row))


@pytest.mark.parametrize(
    "row",
    [
        "rice,pasta_rice_grains,2,cup,2 cup bag",  # rice has no g_per_ml
        "rice,pasta_rice_grains,2,each,2 pieces",  # rice has no g_per_each
        "chicken,meat_seafood,2,each,2 pieces",
    ],
)
def test_pack_unit_no_conversion(tmp_path: Path, row: str) -> None:
    key = row.split(",")[0]
    with pytest.raises(GroceryInfoError, match="column pack_unit"):
        load_grocery_info(CAT, write(tmp_path, **{key: row}))


def test_label_bounds(tmp_path: Path) -> None:
    ok = "x" * 40
    info = load_grocery_info(CAT, write(tmp_path, rice=f"rice,pasta_rice_grains,2,lb,{ok}"))
    assert info.get("rice").pack_label == ok
    for bad in ("", "x" * 41, "   "):
        row = f"rice,pasta_rice_grains,2,lb,{bad}"
        with pytest.raises(GroceryInfoError, match="column pack_label"):
            load_grocery_info(CAT, write(tmp_path, rice=row))


def test_label_is_trimmed_before_length_check(tmp_path: Path) -> None:
    row = f"rice,pasta_rice_grains,2,lb,  {'x' * 40}  "
    assert load_grocery_info(CAT, write(tmp_path, rice=row)).get("rice").pack_label == "x" * 40


@pytest.mark.parametrize(
    "row",
    [
        "rice,not_purchased,2,lb,2 lb bag",
        "rice,not_purchased,2,,",
        "rice,not_purchased,,lb,",
        "rice,not_purchased,,,2 lb bag",
    ],
)
def test_not_purchased_with_pack_rejected(tmp_path: Path, row: str) -> None:
    with pytest.raises(GroceryInfoError, match="must be empty"):
        load_grocery_info(CAT, write(tmp_path, rice=row))


@pytest.mark.parametrize(
    "row",
    [
        "rice,pasta_rice_grains,,,",
        "rice,pasta_rice_grains,,lb,2 lb bag",
        "rice,pasta_rice_grains,2,,2 lb bag",
        "rice,pasta_rice_grains,2,lb,",
    ],
)
def test_purchased_without_pack_rejected(tmp_path: Path, row: str) -> None:
    with pytest.raises(GroceryInfoError, match="required"):
        load_grocery_info(CAT, write(tmp_path, rice=row))


def test_wrong_cell_count_rejected(tmp_path: Path) -> None:
    with pytest.raises(GroceryInfoError, match="expected 5 columns"):
        load_grocery_info(CAT, write(tmp_path, rice="rice,pasta_rice_grains,2,lb"))


def test_error_names_line_and_column(tmp_path: Path) -> None:
    # header is line 1; chicken is line 2, rice line 3
    with pytest.raises(GroceryInfoError, match=r"^line 3, column pack_quantity: ") as exc:
        load_grocery_info(CAT, write(tmp_path, rice="rice,pasta_rice_grains,0,lb,2 lb bag"))
    assert isinstance(exc.value, ValueError)
