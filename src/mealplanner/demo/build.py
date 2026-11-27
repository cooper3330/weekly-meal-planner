"""Build the demo app: migrations, scenario seed data, demo client, fixed clock."""

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from mealplanner.app import DB_FILENAME, MIGRATIONS_DIR, create_app
from mealplanner.config import Settings
from mealplanner.db import apply_migrations, connect
from mealplanner.demo import DEMO_OWNER, FIXED_NOW, DemoError
from mealplanner.demo.client import DemoLLMClient
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


def seed_scenario(db_path: Path, name: str) -> None:
    """Seed ``name`` through the feature packages' own functions. Safe to run twice."""
    scenario = load_scenario(name)
    profile = scenario.get("profile")
    if profile is None:
        return
    conn = connect(db_path)
    try:
        save_profile(conn, DEMO_USER_ID, Profile.model_validate(profile), now=FIXED_NOW)
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
