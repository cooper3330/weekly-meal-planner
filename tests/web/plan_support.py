"""Helpers for the slate page tests: scripted batches over the planning fixture catalog."""

from pathlib import Path
from typing import Any

from mealplanner.nutrition.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]
CAT = load_catalog(ROOT / "tests" / "fixtures" / "catalog" / "planning.csv")

# Each meal holds 200 g of "dense" (500 kcal per 100 g); the bits of the index choose extra
# ingredients, so the ingredient-id sets differ and the same-meal rule keeps them apart.
_POOL = ("chicken", "rice", "egg", "olive_oil", "fuel", "chicken_stock")


def option(
    index: int, *, name: str | None = None, extra: tuple[tuple[str, float, str], ...] = ()
) -> dict[str, Any]:
    ids = [p for bit, p in enumerate(_POOL) if index >> bit & 1]
    lines: list[dict[str, Any]] = [{"ingredient_id": "dense", "quantity": 200, "unit": "g"}]
    lines += [{"ingredient_id": i, "quantity": 100, "unit": "g"} for i in ids]
    lines += [{"ingredient_id": i, "quantity": q, "unit": u} for i, q, u in extra]
    return {
        "name": name or f"Test meal number {index}",
        "description": "A meal for tests.",
        "cook_minutes": 30,
        "tags": ["quick"],
        "ingredients": lines,
    }


def batch(*options: dict[str, Any]) -> dict[str, Any]:
    return {"options": list(options)}


def plain_batch(start: int, count: int) -> dict[str, Any]:
    return batch(*(option(i) for i in range(start, start + count)))


def good_slate(lunches: int = 8, dinners: int = 8) -> list[dict[str, Any]]:
    return [plain_batch(0, lunches), plain_batch(32, dinners)]
