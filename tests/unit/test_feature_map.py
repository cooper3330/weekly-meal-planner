"""The feature map (tests/e2e/feature-map.json) must agree with the app's routes (ADR 0029).

Offline, no browser. The step and locator check below is a minimal interim one; #154 replaces
it with the agentbrowser types, so keep it small.
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.routing import Mount
from starlette.websockets import WebSocket

from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.demo.build import scenario_names
from mealplanner.llm.fake import FakeLLMClient

ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "tests" / "e2e" / "feature-map.json"
FLOWS_DIR = ROOT / "tests" / "flows"

TOP_KEYS = {"internal", "features"}
FEATURE_KEYS = {
    "name",
    "routes",
    "scenario",
    "reach",
    "landmarks",
    "actions",
    "implements",
    "flows",
    "sources",
    "phone_notes",
}
STEP_KEYS = {"goto", "click", "fill", "select", "press", "expect_text", "expect_no_text"}
NAV_KEYS = {"click", "fill", "select", "press"}
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SHARED_LAYOUT = {"base.html", "app.css"}


# --- the routes the app really has ---------------------------------------------------------


def app_routes(app: FastAPI) -> list[str]:
    # FastAPI keeps included routers lazy in app.routes; iter_route_contexts flattens them and
    # gives each route's full path.
    out: list[str] = []
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        if isinstance(route, APIRoute):
            out.extend(f"{m} {ctx.path}" for m in sorted(route.methods) if m != "HEAD")
        elif isinstance(route, Mount):
            out.append(f"MOUNT {ctx.path}")
        else:
            raise AssertionError(f"Unknown route type {type(route).__name__}: {route!r}")
    return out


# --- the check ------------------------------------------------------------------------------


def _locator_errors(where: str, loc: Any, extra: frozenset[str] = frozenset()) -> list[str]:
    if not isinstance(loc, dict):
        return [f"{where}: a locator must be an object"]
    keys = set(loc) - extra
    if keys == {"role", "name"} or keys == {"label"} or keys == {"text"}:
        if all(isinstance(loc[k], str) and loc[k] for k in keys):
            return []
        return [f"{where}: locator values must be non-empty strings"]
    return [f"{where}: locator must be role+name, label or text, got keys {sorted(loc)}"]


def _step_errors(where: str, step: Any) -> list[str]:
    if not isinstance(step, dict):
        return [f"{where}: a step must be an object"]
    keys = set(step) - {"shows"}
    if len(keys) != 1 or not keys <= STEP_KEYS:
        return [f"{where}: a step needs exactly one step key from {sorted(STEP_KEYS)}"]
    (kind,) = keys
    value = step[kind]
    if kind in {"goto", "press", "expect_text", "expect_no_text"}:
        return [] if isinstance(value, str) and value else [f"{where}: {kind} needs text"]
    extra = frozenset({"value"}) if kind in {"fill", "select"} else frozenset()
    return _locator_errors(f"{where}.{kind}", value, extra)


def _reach_errors(name: str, reach: Any) -> list[str]:
    if not isinstance(reach, list) or not reach or reach[0] != {"goto": "/"}:
        return [f'{name}: reach must start with {{"goto": "/"}}']
    errors: list[str] = []
    for i, step in enumerate(reach):
        where = f"{name}: reach[{i}]"
        errors += _step_errors(where, step)
        if i > 0 and isinstance(step, dict):
            if "goto" in step:
                errors.append(f"{where}: no other goto is allowed in reach")
            elif set(step) - {"shows"} and not set(step) & NAV_KEYS:
                errors.append(f"{where}: reach steps must be click, fill, select or press")
    return errors


def _implements_errors(name: str, implements: Any) -> list[str]:
    if not isinstance(implements, list):
        return [f"{name}: implements must be a list"]
    errors: list[str] = []
    for i, item in enumerate(implements):
        where = f"{name}: implements[{i}]"
        if not isinstance(item, dict) or set(item) != {"issue", "criteria"}:
            errors.append(f"{where}: needs exactly issue and criteria")
            continue
        issue = item["issue"]
        if not isinstance(issue, int) or isinstance(issue, bool) or issue < 1:
            errors.append(f"{where}: issue must be a positive number")
        crit = item["criteria"]
        if not isinstance(crit, list) or not crit:
            errors.append(f"{where}: criteria needs at least one AC-n")
        else:
            errors += [
                f"{where}: criteria {c!r} is not AC-n"
                for c in crit
                if not (isinstance(c, str) and re.fullmatch(r"AC-\d+", c))
            ]
    return errors


def _feature_errors(
    f: dict[str, Any], root: Path, flows_dir: Path, scenarios: set[str]
) -> list[str]:
    name = f.get("name", "<unnamed>")
    missing = FEATURE_KEYS - set(f)
    unknown = set(f) - FEATURE_KEYS
    errors = [f"{name}: missing field {k}" for k in sorted(missing)]
    errors += [f"{name}: unknown field {k}" for k in sorted(unknown)]
    if missing:
        return errors
    if not isinstance(name, str) or not KEBAB.match(name):
        errors.append(f"{name}: name must be kebab-case")
    routes = f["routes"]
    if not isinstance(routes, list) or not all(isinstance(r, str) for r in routes):
        return [*errors, f"{name}: routes must be a list of strings"]
    if not any(r.startswith("GET ") for r in routes):
        errors.append(f"{name}: routes needs at least one GET route")
    if f["scenario"] not in scenarios:
        errors.append(f"{name}: scenario {f['scenario']!r} does not exist")
    errors += _reach_errors(name, f["reach"])
    if not isinstance(f["landmarks"], list) or not f["landmarks"]:
        errors.append(f"{name}: landmarks needs at least one landmark")
    else:
        for i, loc in enumerate(f["landmarks"]):
            errors += _locator_errors(f"{name}: landmarks[{i}]", loc)
    for i, action in enumerate(f["actions"]):
        if not isinstance(action, dict) or set(action) != {"control", "result"}:
            errors.append(f"{name}: actions[{i}] needs exactly control and result")
        else:
            errors += _locator_errors(f"{name}: actions[{i}].control", action["control"])
    errors += _implements_errors(name, f["implements"])
    if not isinstance(f["phone_notes"], str):
        errors.append(f"{name}: phone_notes must be text")
    for flow in f["flows"]:
        if not (flows_dir / f"{flow}.json").is_file():
            errors.append(f"{name}: flows: no file tests/flows/{flow}.json")
    sources = f["sources"]
    if not isinstance(sources, list) or not sources:
        errors.append(f"{name}: sources must not be empty")
    else:
        for src in sources:
            if Path(src).name in SHARED_LAYOUT:
                errors.append(f"{name}: sources must not list the shared layout file {src}")
            elif not (root / src).is_file():
                errors.append(f"{name}: sources: {src} does not exist")
    return errors


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
    if not isinstance(feature_map, dict):
        return ["map: must be an object"]
    errors = [f"map: unknown key {k}" for k in sorted(set(feature_map) - TOP_KEYS)]
    errors += [f"map: missing key {k}" for k in sorted(TOP_KEYS - set(feature_map))]
    if errors:
        return errors

    owners: dict[str, list[str]] = {}
    for item in feature_map["internal"]:
        if not isinstance(item, dict) or set(item) != {"route", "why"} or not item["why"]:
            errors.append(f"internal: needs exactly route and a non-empty why: {item!r}")
            continue
        owners.setdefault(item["route"], []).append("internal")

    names: set[str] = set()
    for f in feature_map["features"]:
        errors += _feature_errors(f, root, flows_dir, known)
        name = f.get("name", "<unnamed>")
        if name in names:
            errors.append(f"{name}: duplicate name")
        names.add(name)
        for route in f.get("routes", []):
            owners.setdefault(route, []).append(name)

    have = set(routes)
    for route in sorted(have - set(owners)):
        errors.append(f"route {route} is in no entry and not in internal")
    for route, who in sorted(owners.items()):
        if route not in have:
            errors.append(f"{who[0]}: route {route} does not exist in the app")
        if len(who) > 1:
            errors.append(f"route {route} is listed more than once: {', '.join(who)}")
    return errors


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
        "profile",
        "profile-edit",
        "profile-history",
        "profile-version",
    }
    assert {i["route"] for i in real_map["internal"]} == {"GET /healthz", "MOUNT /static"}
    assert entry(real_map, "home")["reach"] == [{"goto": "/"}]


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
    ("not-kebab", _set("name", "Profile_Edit"), "kebab-case"),
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
    entry(real_map, "profile")["flows"] = ["ok"]
    assert check_map(real_map, app_routes(app), flows_dir=tmp_path) == []


def test_duplicate_name_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    entry(real_map, "profile-history")["name"] = "profile"
    assert "profile: duplicate name" in joined(check_map(real_map, app_routes(app)))


def test_unknown_top_level_key_fails(app: FastAPI, real_map: dict[str, Any]) -> None:
    real_map["extra"] = []
    assert "extra" in joined(check_map(real_map, app_routes(app)))
