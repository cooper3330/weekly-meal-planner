"""Shared helpers for the slate tests: a migrated database, a catalog, and scripted batches."""

import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mealplanner.db.connection import connect
from mealplanner.db.migrate import apply_migrations
from mealplanner.nutrition.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "src" / "mealplanner" / "db" / "migrations"
CAT = load_catalog(ROOT / "tests" / "fixtures" / "catalog" / "planning.csv")

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
WEEK = date(2026, 11, 2)

# Every generated meal holds 200 g of "dense" (500 kcal per 100 g), so it is 500 kcal per
# serving at 2 servings; the bits of the index choose extra ingredients, so ids sets differ.
_POOL = ("chicken", "rice", "egg", "olive_oil", "fuel", "chicken_stock")


def option(
    index: int,
    *,
    name: str | None = None,
    extra: Sequence[tuple[str, float, str]] = (),
    cook_minutes: int = 30,
) -> dict[str, Any]:
    ids = [p for bit, p in enumerate(_POOL) if index >> bit & 1]
    lines: list[dict[str, Any]] = [{"ingredient_id": "dense", "quantity": 200, "unit": "g"}]
    lines += [{"ingredient_id": i, "quantity": 100, "unit": "g"} for i in ids]
    lines += [{"ingredient_id": i, "quantity": q, "unit": u} for i, q, u in extra]
    return {
        "name": name or f"Test meal number {index}",
        "description": "A meal for tests.",
        "cook_minutes": cook_minutes,
        "tags": ["quick"],
        "ingredients": lines,
    }


def batch(*options: dict[str, Any]) -> dict[str, Any]:
    return {"options": list(options)}


def plain_batch(start: int, count: int) -> dict[str, Any]:
    return batch(*(option(i) for i in range(start, start + count)))


LUNCH_START = 0
DINNER_START = 32


def good_slate_responses(lunches: int = 8, dinners: int = 8) -> list[dict[str, Any]]:
    return [plain_batch(LUNCH_START, lunches), plain_batch(DINNER_START, dinners)]


def migrated(tmp_path: Path) -> Path:
    path = tmp_path / "app.sqlite3"
    apply_migrations(path, MIGRATIONS, tmp_path / "backups")
    return path


def open_db(tmp_path: Path) -> sqlite3.Connection:
    return connect(migrated(tmp_path))


def count_rows(conn: sqlite3.Connection) -> int:
    n1 = conn.execute("SELECT COUNT(*) FROM slates").fetchone()[0]
    n2 = conn.execute("SELECT COUNT(*) FROM slate_options").fetchone()[0]
    return int(n1) + int(n2)
