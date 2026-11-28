"""Build the demo app: migrations, scenario seed data, demo client, fixed clock."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from mealplanner.app import DB_FILENAME, MIGRATIONS_DIR, create_app
from mealplanner.config import Settings
from mealplanner.db import apply_migrations, connect
from mealplanner.demo import DEMO_OWNER, FIXED_NOW, DemoError
from mealplanner.demo.client import DemoLLMClient
from mealplanner.nutrition.catalog import Catalog, load_catalog
from mealplanner.planning.generate import generate_slate
from mealplanner.planning.models import MealOption, MealType
from mealplanner.planning.selection import confirm_selection, pick_option
from mealplanner.planning.store import get_slate
from mealplanner.planning.week import week_start_for
from mealplanner.profile.models import Profile
from mealplanner.profile.store import save_profile

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
DEMO_USER_ID = 1  # the single user (ADR 0002); the app stores the owner login on this row
LOOPBACK = "127.0.0.1"


def scenario_names() -> list[str]:
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.json"))


def load_scenario(name: str) -> dict[str, Any]:
    path = SCENARIOS_DIR / f"{name}.json"
    if not name.isidentifier() or not path.is_file():
        raise DemoError(f"Unknown scenario {name!r}; available: {', '.join(scenario_names())}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


class _WeekClient:
    """Answers the slate's batch calls from the scenario: lunches first, then dinners."""

    def __init__(self, week: dict[str, Any]) -> None:
        self._batches = [week["lunches"], week["dinners"]]

    def generate(
        self, *, system: str, messages: list[Any], output: type[Any], max_tokens: int
    ) -> Any:
        return output.model_validate({"options": self._batches.pop(0)})


def _seed_week(conn: sqlite3.Connection, catalog: Catalog, week: dict[str, Any]) -> None:
    """Generate, pick and confirm the week of ``FIXED_NOW`` through the planning functions."""
    week_start = week_start_for(FIXED_NOW.date())
    if get_slate(conn, DEMO_USER_ID, week_start) is not None:
        return  # already seeded
    view = generate_slate(
        conn,
        _WeekClient(week),
        catalog,
        DEMO_USER_ID,
        week_start=week_start,
        now=FIXED_NOW,
        lunch_count=len(week["lunches"]),
        dinner_count=len(week["dinners"]),
    )
    ids = {
        slot.draft.name: slot.option_id
        for meal_type in MealType
        for slot in view.options[meal_type]
        if isinstance(slot, MealOption) and slot.option_id is not None
    }
    for name in week["picks"]:
        if name not in ids:
            raise DemoError(f"Scenario picks {name!r}, which is not one of its options")
        pick_option(conn, catalog, DEMO_USER_ID, ids[name], now=FIXED_NOW)
    confirm_selection(conn, catalog, DEMO_USER_ID, week_start, now=FIXED_NOW)


def seed_scenario(db_path: Path, name: str) -> None:
    """Seed ``name`` through the feature packages' own functions. Safe to run twice."""
    scenario = load_scenario(name)
    profile = scenario.get("profile")
    if profile is None:
        return
    conn = connect(db_path)
    try:
        save_profile(conn, DEMO_USER_ID, Profile.model_validate(profile), now=FIXED_NOW)
        week = scenario.get("week")
        if week is not None:
            _seed_week(conn, load_catalog(), week)
    finally:
        conn.close()


def build_demo_app(
    data_dir: Path, scenario: str, *, host: str = LOOPBACK, port: int = 8000
) -> FastAPI:
    load_scenario(scenario)  # fail on an unknown name before touching the disk
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / DB_FILENAME
    apply_migrations(db_path, MIGRATIONS_DIR, data_dir / "backups")
    seed_scenario(db_path, scenario)
    # Built directly, never from the environment: no bridge or key setting is read in demo mode.
    settings = Settings(
        host=host, port=port, data_dir=data_dir, allowed_hosts=(), owner_login=DEMO_OWNER
    )
    return create_app(settings, llm_client=DemoLLMClient(), clock=lambda: FIXED_NOW)
