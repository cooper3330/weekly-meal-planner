"""Helpers for the recipe tests: meals built through ``check_option``, and step payloads."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mealplanner.nutrition.catalog import load_catalog
from mealplanner.planning.checks import check_option
from mealplanner.planning.models import MealOption, Rejection
from mealplanner.planning.schema import MealOptionDraft

ROOT = Path(__file__).resolve().parents[3]
CAT = load_catalog(ROOT / "tests" / "fixtures" / "catalog" / "recipes.csv")


def make_meal(
    ids: Sequence[str] = ("chicken", "rice"),
    *,
    servings: int = 2,
    cook_minutes: int = 30,
    name: str = "Chicken and rice",
    unit: str = "g",
    quantity: float = 100,
    option_id: int | None = None,
) -> MealOption:
    """A checked meal: 200 g of "dense" (500 kcal per serving for 2), plus each id."""
    lines: list[dict[str, Any]] = [{"ingredient_id": "dense", "quantity": 200, "unit": "g"}]
    lines += [{"ingredient_id": i, "quantity": quantity, "unit": unit} for i in ids]
    draft = MealOptionDraft.model_validate(
        {
            "name": name,
            "description": "A meal for tests.",
            "cook_minutes": cook_minutes,
            "tags": ["quick"],
            "ingredients": lines,
        }
    )
    result = check_option(
        draft, catalog=CAT, servings=servings, max_cook_minutes=240, exclusions=frozenset()
    )
    assert not isinstance(result, Rejection), result
    if option_id is None:
        return result
    return MealOption(
        draft=result.draft,
        servings=result.servings,
        nutrition=result.nutrition,
        option_id=option_id,
    )


def meal_ids(meal: MealOption) -> list[str]:
    return [line.ingredient_id for line in meal.draft.ingredients]


def valid_payload(meal: MealOption, *, prep: int = 10) -> dict[str, Any]:
    """Step JSON that passes every rule: every id used, no amounts."""
    return {
        "prep_minutes": prep,
        "steps": [
            {"text": "Get everything ready.", "ingredient_ids": meal_ids(meal)},
            {"text": "Cook it all together, then serve.", "ingredient_ids": []},
        ],
    }


def payload_with_text(meal: MealOption, text: str, *, ids: Sequence[str] | None = None) -> Any:
    """A valid payload whose second step has ``text`` (and optionally these ids)."""
    payload = valid_payload(meal)
    payload["steps"][1] = {"text": text, "ingredient_ids": list(ids or [])}
    return payload


# --- A confirmed week through the real stores (the recipe service tests) ---------------------
# The slate catalog is the planning one, because that is the catalog the fixture slates use.

import sqlite3  # noqa: E402
from datetime import timedelta  # noqa: E402

from planning.slate_support import CAT as PCAT  # noqa: E402
from planning.slate_support import NOW, WEEK, good_slate_responses, migrated  # noqa: E402

from mealplanner.db.connection import connect  # noqa: E402
from mealplanner.llm.fake import FakeLLMClient  # noqa: E402
from mealplanner.planning.generate import generate_slate  # noqa: E402
from mealplanner.planning.models import ConfirmedPlan  # noqa: E402
from mealplanner.planning.selection import (  # noqa: E402
    confirm_selection,
    get_confirmed_plan,
    pick_option,
)
from mealplanner.profile.models import DEFAULT_PROFILE  # noqa: E402
from mealplanner.profile.store import save_profile  # noqa: E402

LATER = NOW + timedelta(minutes=5)
GEN_NOW = NOW + timedelta(minutes=10)

__all__ = ["GEN_NOW", "LATER", "NOW", "PCAT", "WEEK"]


def open_week(tmp_path: Path) -> sqlite3.Connection:
    """A migrated file with user 1's default profile and a slate of 8 lunches and 8 dinners."""
    conn = connect(migrated(tmp_path))
    save_profile(conn, 1, DEFAULT_PROFILE, now=NOW)
    make_user_slate(conn, 1)
    return conn


def make_user_slate(conn: sqlite3.Connection, user_id: int) -> None:
    generate_slate(
        conn,
        FakeLLMClient(good_slate_responses()),
        PCAT,
        user_id,
        week_start=WEEK,
        now=NOW,
        lunch_count=8,
        dinner_count=8,
    )


def add_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        "INSERT INTO users (id, created_at) VALUES (?, '2026-01-01T00:00:00Z')", (user_id,)
    )
    save_profile(conn, user_id, DEFAULT_PROFILE, now=NOW)
    make_user_slate(conn, user_id)


def option_ids(conn: sqlite3.Connection, meal_type: str, user_id: int = 1) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM slate_options WHERE user_id = ? AND meal_type = ? "
        "AND replaced_at IS NULL ORDER BY position",
        (user_id, meal_type),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def pick(conn: sqlite3.Connection, *ids: int, user_id: int = 1) -> None:
    for i in ids:
        pick_option(conn, PCAT, user_id, i, now=NOW)


def confirmed_week(
    tmp_path: Path, *, lunches: int = 3, dinners: int = 2
) -> tuple[sqlite3.Connection, ConfirmedPlan]:
    """Open week, the first ``lunches`` lunches and ``dinners`` dinners picked, confirmed."""
    conn = open_week(tmp_path)
    pick(conn, *option_ids(conn, "lunch")[:lunches], *option_ids(conn, "dinner")[:dinners])
    plan = confirm_selection(conn, PCAT, 1, WEEK, now=LATER)
    return conn, plan


def current_plan(conn: sqlite3.Connection, user_id: int = 1) -> ConfirmedPlan:
    plan = get_confirmed_plan(conn, PCAT, user_id, WEEK)
    assert plan is not None
    return plan


def fake_for(*meals: MealOption) -> FakeLLMClient:
    return FakeLLMClient([valid_payload(m) for m in meals])
