"""Per-ingredient aisle and smallest retail pack size, loaded from a hand-curated CSV (ADR 0023).

Holds no nutrition values. Core module: imports only the catalog and units.
"""

import csv
import math
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from mealplanner.grocery.aisles import Aisle
from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.units import Unit, can_convert

COLUMNS = ("id", "aisle", "pack_quantity", "pack_unit", "pack_label")
_PACK_COLUMNS = COLUMNS[2:]
_MAX_LABEL = 40


class GroceryInfoError(ValueError):
    """The grocery file is malformed. The message names the line and column."""


@dataclass(frozen=True)
class GroceryItemInfo:
    id: str
    aisle: Aisle
    pack_quantity: float | None
    pack_unit: Unit | None
    pack_label: str | None


@dataclass(frozen=True)
class GroceryInfo:
    _items: dict[str, GroceryItemInfo]

    def get(self, id: str) -> GroceryItemInfo:
        return self._items[id]

    def __contains__(self, id: object) -> bool:
        return id in self._items

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))


def _default_grocery_path() -> Path:
    """Location of the packaged file (replaced in tests)."""
    return Path(str(resources.files("mealplanner.grocery") / "data" / "grocery_items.csv"))


def _fail(line: int, column: str, message: str) -> GroceryInfoError:
    return GroceryInfoError(f"line {line}, column {column}: {message}")


def _parse_row(cells: list[str], line: int, catalog: Catalog) -> GroceryItemInfo:
    if len(cells) != len(COLUMNS):
        raise _fail(line, "row", f"expected {len(COLUMNS)} columns, got {len(cells)}")
    id, aisle_raw, qty_raw, unit_raw, label = (c.strip() for c in cells)
    if id not in catalog:
        raise _fail(line, "id", f"not in the catalog: {id!r}")
    try:
        aisle = Aisle(aisle_raw)
    except ValueError:
        raise _fail(line, "aisle", f"unknown aisle {aisle_raw!r}") from None

    pack = dict(zip(_PACK_COLUMNS, (qty_raw, unit_raw, label), strict=True))
    if aisle is Aisle.not_purchased:
        for column, value in pack.items():
            if value != "":
                raise _fail(line, column, "must be empty for aisle not_purchased")
        return GroceryItemInfo(id, aisle, None, None, None)
    for column, value in pack.items():
        if value == "":
            raise _fail(line, column, f"required for aisle {aisle.value}")

    try:
        quantity = float(qty_raw)
    except ValueError:
        raise _fail(line, "pack_quantity", f"not a number: {qty_raw!r}") from None
    if not math.isfinite(quantity) or quantity <= 0:
        raise _fail(line, "pack_quantity", f"must be positive and finite, got {qty_raw!r}")
    try:
        unit = Unit(unit_raw)
    except ValueError:
        raise _fail(line, "pack_unit", f"unknown unit {unit_raw!r}") from None
    if not can_convert(unit, catalog.get(id)):
        raise _fail(line, "pack_unit", f"cannot convert {unit_raw!r} for {id!r}")
    if len(label) > _MAX_LABEL:
        raise _fail(line, "pack_label", f"longer than {_MAX_LABEL} characters")
    return GroceryItemInfo(id, aisle, quantity, unit, label)


def load_grocery_info(catalog: Catalog, path: Path | None = None) -> GroceryInfo:
    """Load and validate the grocery CSV against ``catalog``; the packaged file when None."""
    if path is None:
        path = _default_grocery_path()
    items: dict[str, GroceryItemInfo] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        if tuple(c.strip() for c in header) != COLUMNS:
            raise _fail(1, "header", f"expected {','.join(COLUMNS)}, got {','.join(header)}")
        for cells in reader:
            if not cells:
                continue
            line = reader.line_num
            item = _parse_row(cells, line, catalog)
            if item.id in items:
                raise _fail(line, "id", f"duplicate id {item.id!r}")
            items[item.id] = item
    for id in catalog.ids():
        if id not in items:
            raise _fail(1, "id", f"no row for catalog id {id!r}")
    return GroceryInfo(items)
