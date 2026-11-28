"""The feature map (tests/e2e/feature-map.json) must agree with the app's routes (ADR 0029).

Offline, no browser. The map's schema and rules live in `agentbrowser.feature_map` (ADR 0028's
steps and locators); this module checks the real map against the real app and keeps the cases
that guard each rule.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.websockets import WebSocket

from agentbrowser.feature_map import (
    FeatureMapError,
    app_routes,
    parse,
    route_errors,
)
from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.demo.build import scenario_names
from mealplanner.llm.fake import FakeLLMClient

ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "tests" / "e2e" / "feature-map.json"
FLOWS_DIR = ROOT / "tests" / "flows"
SCENARIOS_DIR = ROOT / "src" / "mealplanner" / "demo" / "scenarios"


# --- the check ------------------------------------------------------------------------------


def check_map(
    feature_map: Any,
    routes: list[str],
    *,
    root: Path = ROOT,
    flows_dir: Path = FLOWS_DIR,
    scenarios: set[str] | None = None,
) -> list[str]:
    """Every problem found, each naming the entry (or route) and the field."""
    known = set(scenario_names()) if scenarios is None else scenarios
    try:
        parsed = parse(feature_map, root=root, flows_dir=flows_dir, scenarios=known)
    except FeatureMapError as exc:
        return exc.errors
    return route_errors(parsed, routes)


# --- fixtures -------------------------------------------------------------------------------


def make_app(tmp_path: Path) -> FastAPI:
    settings = load_settings(
        {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}
    )
    return create_app(settings, llm_client=FakeLLMClient([]))


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path)


@pytest.fixture
def real_map() -> dict[str, Any]:
    assert MAP_PATH.is_file(), f"{MAP_PATH} is missing"
    data: dict[str, Any] = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def entry(fmap: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = next(f for f in fmap["features"] if f["name"] == name)
    return found


def joined(errors: list[str]) -> str:
    return "\n".join(errors)


# --- the real map against the real app ------------------------------------------------------


def test_real_map_matches_real_app(app: FastAPI, real_map: dict[str, Any]) -> None:
    assert check_map(real_map, app_routes(app)) == []


def test_real_map_has_the_expected_pages(real_map: dict[str, Any]) -> None:
    assert {f["name"] for f in real_map["features"]} >= {
        "home",
        "grocery",
        "profile",
        "profile-edit",
        "profile-history",
        "profile-version",
    }
    assert {i["route"] for i in real_map["internal"]} == {"GET /healthz", "MOUNT /static"}
    assert entry(real_map, "home")["reach"] == [{"goto": "/"}]


def test_every_scenario_an_entry_names_exists_as_a_file(real_map: dict[str, Any]) -> None:
    for f in real_map["features"]:
        assert (SCENARIOS_DIR / f"{f['scenario']}.json").is_file(), f["name"]


def test_app_routes_lists_each_method_once(app: FastAPI) -> None:
    routes = app_routes(app)
    assert "GET /profile/history/{version}" in routes
    assert "POST /profile" in routes
    assert "MOUNT /static" in routes
    assert not any(r.startswith("HEAD ") for r in routes)


# --- route rule ----------------------------------------------------------------------------


def test_extra_app_route_fails_and_is_named(app: FastAPI, real_map: dict[str, Any]) -> None:
    def extra() -> None:
        return None

    app.add_api_route("/extra", extra, methods=["GET"])
    assert "GET /extra" in joined(check_map(real_map, app_routes(app)))


def test_unknown_route_type_fails_and_is_named(app: FastAPI) -> None:
    from starlette.routing import WebSocketRoute

    async def ws(websocket: WebSocket) -> None:
        return None

    app.router.routes.append(WebSocketRoute("/ws", ws))
    with pytest.raises(AssertionError, match="WebSocketRoute"):
        app_routes(app)


def test_entry_route_the_app_lacks_fails_and_names_entry(
    app: FastAPI, real_map: dict[str, Any]
) -> None:
    entry(real_map, "home")["routes"].append("GET /gone")
    errors = joined(check_map(real_map, app_routes(app)))
    assert "home" in errors
    assert "GET /gone" in errors


def test_internal_route_the_app_lacks_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    real_map["internal"].append({"route": "GET /gone", "why": "test"})
    errors = joined(check_map(real_map, app_routes(app)))
    assert "internal" in errors
    assert "GET /gone" in errors


def test_removing_healthz_from_internal_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    real_map["internal"] = [i for i in real_map["internal"] if i["route"] != "GET /healthz"]
    assert "GET /healthz" in joined(check_map(real_map, app_routes(app)))


def test_concrete_version_path_is_not_the_route(app: FastAPI, real_map: dict[str, Any]) -> None:
    routes = entry(real_map, "profile-version")["routes"]
    routes[:] = ["GET /profile/history/1"]
    errors = joined(check_map(real_map, app_routes(app)))
    assert "profile-version" in errors
    assert "GET /profile/history/1" in errors
    assert "GET /profile/history/{version}" in errors  # now unowned


def test_post_route_missing_from_every_entry_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    entry(real_map, "profile-edit")["routes"].remove("POST /profile")
    assert "POST /profile" in joined(check_map(real_map, app_routes(app)))


def test_route_in_two_entries_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    entry(real_map, "profile")["routes"].append("GET /profile/history")
    errors = joined(check_map(real_map, app_routes(app)))
    assert "GET /profile/history" in errors
    assert "more than once" in errors


# --- schema rules --------------------------------------------------------------------------

Mutation = tuple[str, Any]


def _drop(key: str) -> Any:
    return lambda f: f.pop(key)


def _set(key: str, value: Any) -> Any:
    return lambda f: f.__setitem__(key, value)


SCHEMA_CASES: list[tuple[str, Any, str]] = [
    ("missing-field", _drop("phone_notes"), "phone_notes"),
    ("unknown-key", _set("colour", "red"), "colour"),
    ("not-kebab", _set("name", "Profile_Edit"), "name must match"),
    ("no-get", _set("routes", ["POST /profile"]), "GET"),
    ("no-landmarks", _set("landmarks", []), "landmarks"),
    ("no-sources", _set("sources", []), "sources"),
    ("reach-not-home", _set("reach", [{"click": {"role": "link", "name": "x"}}]), "reach"),
    (
        "second-goto",
        _set("reach", [{"goto": "/"}, {"goto": "/profile"}]),
        "goto",
    ),
    (
        "expect-text-in-reach",
        _set("reach", [{"goto": "/"}, {"expect_text": "hi"}]),
        "reach[1]",
    ),
    (
        "expect-in-reach",
        _set("reach", [{"goto": "/"}, {"expect": {"text": "hi"}}]),
        "reach[1]",
    ),
    (
        "two-step-keys",
        _set(
            "reach",
            [{"goto": "/"}, {"click": {"role": "link", "name": "a"}, "press": "Enter"}],
        ),
        "reach[1]",
    ),
    ("css-locator", _set("landmarks", [{"css": "h1"}]), "landmarks[0]"),
    ("implements-issue-0", _set("implements", [{"issue": 0, "criteria": ["AC-1"]}]), "issue"),
    ("implements-fr", _set("implements", [{"issue": 6, "criteria": ["FR-1"]}]), "FR-1"),
    ("missing-source", _set("sources", ["src/nope.html"]), "src/nope.html"),
    ("layout-source", _set("sources", ["src/mealplanner/web/templates/base.html"]), "base.html"),
    ("css-source", _set("sources", ["src/mealplanner/web/static/app.css"]), "app.css"),
    ("unknown-scenario", _set("scenario", "nope"), "scenario"),
    ("unknown-flow", _set("flows", ["nope"]), "nope"),
]


@pytest.mark.parametrize(
    ("case", "mutate", "needle"), SCHEMA_CASES, ids=[c[0] for c in SCHEMA_CASES]
)
def test_schema_violation_names_entry_and_field(
    app: FastAPI, real_map: dict[str, Any], case: str, mutate: Any, needle: str
) -> None:
    mutate(entry(real_map, "profile-history"))
    errors = check_map(real_map, app_routes(app))
    relevant = [e for e in errors if needle in e]
    assert relevant, f"{case}: no error mentions {needle!r}: {errors}"
    if case != "not-kebab":
        assert any("profile-history" in e for e in relevant)


def test_flow_error_names_entry_and_flow(app: FastAPI, real_map: dict[str, Any]) -> None:
    entry(real_map, "profile")["flows"] = ["nope"]
    errors = joined(check_map(real_map, app_routes(app)))
    assert "profile: flows: no file tests/flows/nope.json" in errors


def test_existing_flow_file_is_accepted(
    app: FastAPI, real_map: dict[str, Any], tmp_path: Path
) -> None:
    (tmp_path / "ok.json").write_text("{}", encoding="utf-8")
    (tmp_path / "home.json").write_text("{}", encoding="utf-8")  # the home entry lists its flow
    entry(real_map, "profile")["flows"] = ["ok"]
    assert check_map(real_map, app_routes(app), flows_dir=tmp_path) == []


def test_duplicate_name_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    entry(real_map, "profile-history")["name"] = "profile"
    assert "profile: duplicate name" in joined(check_map(real_map, app_routes(app)))


def test_unknown_top_level_key_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    real_map["extra"] = []
    assert "extra" in joined(check_map(real_map, app_routes(app)))
