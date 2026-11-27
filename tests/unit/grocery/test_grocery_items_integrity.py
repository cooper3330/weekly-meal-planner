"""Integrity of the packaged grocery file against the packaged catalog (ADR 0023)."""

import csv
import re
from pathlib import Path

import pytest

from mealplanner.grocery import info as info_mod
from mealplanner.grocery.aisles import Aisle
from mealplanner.grocery.info import load_grocery_info
from mealplanner.nutrition.catalog import load_catalog
from mealplanner.nutrition.units import to_grams

CATALOG = load_catalog()
PATH = info_mod._default_grocery_path()
INFO = load_grocery_info(CATALOG)

# Rows that nobody buys. Anything added here needs a reason.
NOT_PURCHASED_ALLOWLIST = {"water"}
# Rows allowed to fall outside the 1 g to 5000 g plausibility band: none so far.
PACK_GRAMS_EXCEPTIONS: set[str] = set()

# ADR 0023 table: value and label, in print order.
ADR_AISLES = [
    ("produce", "Produce"),
    ("bakery", "Bakery and bread"),
    ("meat_seafood", "Meat and seafood"),
    ("dairy_eggs", "Dairy and eggs"),
    ("pasta_rice_grains", "Pasta, rice and grains"),
    ("canned_jarred", "Canned and jarred"),
    ("baking_spices", "Baking and spices"),
    ("oils_condiments_sauces", "Oils, condiments and sauces"),
    ("international", "International"),
    ("snacks_nuts_dried_fruit", "Snacks, nuts and dried fruit"),
    ("beverages", "Beverages"),
    ("frozen", "Frozen"),
    ("not_purchased", "Not bought (tap water)"),
]


def test_packaged_file_covers_catalog() -> None:
    with open(PATH, newline="", encoding="utf-8") as f:
        ids = [row["id"] for row in csv.DictReader(f)]
    assert sorted(ids) == list(CATALOG.ids())
    assert len(ids) == len(set(ids))


def test_packaged_file_is_sorted_by_id() -> None:
    with open(PATH, newline="", encoding="utf-8") as f:
        ids = [row["id"] for row in csv.DictReader(f)]
    assert ids == sorted(ids)


def test_packaged_file_loads() -> None:
    assert load_grocery_info(CATALOG).ids() == CATALOG.ids()


def test_packaged_file_has_no_nutrition_columns() -> None:
    with open(PATH, newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == ["id", "aisle", "pack_quantity", "pack_unit", "pack_label"]


def test_every_aisle_label_nonempty() -> None:
    assert all(a.label.strip() for a in Aisle)


def test_aisle_order_matches_adr() -> None:
    assert [(a.value, a.label) for a in Aisle] == ADR_AISLES


def test_water_not_purchased() -> None:
    assert INFO.get("water").aisle is Aisle.not_purchased
    unbought = {i for i in INFO.ids() if INFO.get(i).aisle is Aisle.not_purchased}
    assert unbought == NOT_PURCHASED_ALLOWLIST


@pytest.mark.parametrize("id", [i for i in CATALOG.ids() if i not in NOT_PURCHASED_ALLOWLIST])
def test_pack_grams_plausible(id: str) -> None:
    item = INFO.get(id)
    assert item.pack_quantity is not None and item.pack_unit is not None
    grams = to_grams(item.pack_quantity, item.pack_unit, CATALOG.get(id))
    if id in PACK_GRAMS_EXCEPTIONS:
        return
    assert 1 <= grams <= 5000, f"{id}: {grams:.1f} g"


def test_packaged_path_exists() -> None:
    assert Path(PATH).is_file()


_WEIGHT_G = {"lb": 453.592, "oz": 28.3495, "g": 1.0, "kg": 1000.0}
_VOLUME_ML = {"fl oz": 29.5735, "ml": 1.0, "l": 1000.0}


def test_pack_label_agrees_with_quantity() -> None:
    """A label that states a weight or volume must match pack_quantity."""
    pattern = re.compile(r"^([\d.]+)\s*(lb|oz|fl oz|g|kg|ml|l)\b")
    with open(PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    bad = []
    for row in rows:
        m = pattern.match(row["pack_label"])
        if not m or not row["pack_unit"]:
            continue
        table = _WEIGHT_G if row["pack_unit"] == "g" else _VOLUME_ML
        if m.group(2) not in table:
            continue
        stated = float(m.group(1)) * table[m.group(2)]
        if abs(stated - float(row["pack_quantity"])) / stated > 0.03:
            bad.append(row["id"])
    assert bad == []
