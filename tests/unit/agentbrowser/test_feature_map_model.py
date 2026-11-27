"""The feature map models, loader and helpers (#154, ADR 0029). Pure Python, no browser."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from agentbrowser import feature_map as fm
from agentbrowser.flow import Locator, Step

REAL_MAP = fm.REPO_ROOT / "tests" / "e2e" / "feature-map.json"


def click(name: str, role: str = "link") -> dict[str, Any]:
    return {"click": {"role": role, "name": name}}


def feature(name: str, reach: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "routes": [f"GET /{name}"],
        "scenario": "empty",
        "reach": reach,
        "landmarks": [{"role": "heading", "name": name}, {"label": "Field"}],
        "actions": [],
        "implements": [],
        "flows": [],
        "sources": ["src/mealplanner/web/routes.py"],
        "phone_notes": "",
    }
    base.update(over)
    return base


HOME = [{"goto": "/"}]


def build(*features: dict[str, Any]) -> dict[str, Any]:
    return {"internal": [], "features": list(features)}


@pytest.fixture
def real() -> fm.FeatureMap:
    return fm.load(REAL_MAP)


def by_name(name: str, data: dict[str, Any] | None = None) -> fm.Feature:
    parsed = fm.parse(data) if data is not None else fm.load(REAL_MAP)
    return next(f for f in parsed.features if f.name == name)


# --- loading -------------------------------------------------------------------------------


def test_the_real_map_loads(real: fm.FeatureMap) -> None:
    assert {f.name for f in real.features} >= {"home", "profile", "profile-version"}
    assert isinstance(real.features[0].reach[0], Step)
    assert isinstance(real.features[0].landmarks[0], Locator)


def test_a_map_with_no_features_is_valid() -> None:
    assert fm.parse({"internal": [], "features": []}).features == []


def test_load_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(fm.FeatureMapError, match="cannot read"):
        fm.load(tmp_path / "nope.json")


def test_load_reports_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text("{nope")
    with pytest.raises(fm.FeatureMapError, match="not valid JSON"):
        fm.load(path)


def test_load_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(build(feature("home", HOME))))
    assert [f.name for f in fm.load(path).features] == ["home"]


def test_an_error_names_the_entry_and_the_indexed_field() -> None:
    bad = feature("home", [{"goto": "/"}, {"goto": "/x"}])
    with pytest.raises(fm.FeatureMapError) as info:
        fm.parse(build(bad))
    text = str(info.value)
    assert "home" in text and "reach[1]" in text and "goto" in text


def test_every_problem_is_listed() -> None:
    one = feature("one", HOME, landmarks=[])
    two = feature("two", HOME, sources=[])
    with pytest.raises(fm.FeatureMapError) as info:
        fm.parse(build(one, two))
    text = "\n".join(info.value.errors)
    assert "one: landmarks" in text and "two: sources" in text


def test_extra_keys_are_rejected_everywhere() -> None:
    data = build(feature("home", HOME))
    data["features"][0]["landmarks"][0]["css"] = "h1"
    with pytest.raises(fm.FeatureMapError, match=r"landmarks\[0\]"):
        fm.parse(data)


# --- to_walk_flow --------------------------------------------------------------------------


def test_to_walk_flow_is_the_reach_steps_then_one_expect_per_landmark() -> None:
    entry = by_name("profile-edit")
    flow = entry_flow = fm.to_walk_flow(entry)
    assert entry_flow.mode == "demo"
    assert flow.scenario == entry.scenario == "profile"
    assert flow.viewport == "360x640" and (flow.width, flow.height) == (360, 640)
    reach = len(entry.reach)
    assert flow.steps[:reach] == entry.reach
    expects = flow.steps[reach:]
    assert [s.kind for s in expects] == ["expect"] * len(entry.landmarks)
    assert [s.expect for s in expects] == entry.landmarks
    assert len(flow.steps) == reach + len(entry.landmarks)


def test_the_walk_flow_declares_no_proves() -> None:
    assert fm.to_walk_flow(by_name("home")).proves == []


# --- neighbours ----------------------------------------------------------------------------


def test_profile_has_parent_home_and_children_edit_and_history(real: fm.FeatureMap) -> None:
    assert fm.neighbours(real, "profile") == ["home", "profile-edit", "profile-history"]


def test_home_has_no_parent_only_its_child(real: fm.FeatureMap) -> None:
    assert fm.neighbours(real, "home") == ["profile"]


def test_a_leaf_has_only_its_parent(real: fm.FeatureMap) -> None:
    assert fm.neighbours(real, "profile-version") == ["profile-history"]


def test_the_parent_is_the_longest_proper_prefix() -> None:
    a = [*HOME, click("A")]
    b = [*a, click("B")]
    c = [*b, click("C")]
    parsed = fm.parse(
        build(feature("home", HOME), feature("a", a), feature("b", b), feature("c", c))
    )
    assert fm.neighbours(parsed, "c") == ["b"]
    assert fm.neighbours(parsed, "b") == ["a", "c"]
    assert fm.neighbours(parsed, "home") == ["a"]


def test_an_entry_that_is_not_a_prefix_of_another_has_no_children() -> None:
    parsed = fm.parse(
        build(
            feature("home", HOME),
            feature("a", [*HOME, click("A")]),
            feature("b", [*HOME, click("B")]),
        )
    )
    assert fm.neighbours(parsed, "a") == ["home"]
    assert fm.neighbours(parsed, "b") == ["home"]


def test_steps_that_differ_only_in_a_locator_name_are_not_a_prefix_match() -> None:
    parsed = fm.parse(
        build(
            feature("one", [*HOME, click("Profile")]),
            feature("two", [*HOME, click("Profil"), click("Edit")]),
        )
    )
    assert fm.neighbours(parsed, "one") == []
    assert fm.neighbours(parsed, "two") == []


def test_steps_that_differ_only_in_role_are_not_a_prefix_match() -> None:
    parsed = fm.parse(
        build(
            feature("one", [*HOME, click("Go", "link")]),
            feature("two", [*HOME, click("Go", "button"), click("Next")]),
        )
    )
    assert fm.neighbours(parsed, "two") == []


def test_neighbours_of_an_unknown_name_fails() -> None:
    with pytest.raises(KeyError):
        fm.neighbours(fm.parse(build()), "nope")


# --- the path rule -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/profile/history/3", True),
        ("/profile/history/3/x", False),
        ("/profile", False),
        ("/profile/history/", False),
        ("/profile/history", False),
        ("/profile/other/3", False),
    ],
)
def test_path_matches_one_segment_per_parameter(path: str, expected: bool) -> None:
    assert fm.path_matches(path, ["/profile/history/{version}"]) is expected


def test_path_matches_any_template_and_exact_paths() -> None:
    assert fm.path_matches("/profile", ["/", "/profile"])
    assert fm.path_matches("/", ["/"])
    assert not fm.path_matches("/profile/", ["/profile"])


def test_the_walk_checks_the_get_templates_after_the_last_reach_step() -> None:
    entry = by_name("profile-edit")
    check = fm.path_check(entry)
    assert check.after_step == len(entry.reach)
    assert check.templates == ["/profile/edit"]  # the POST is not a page


# --- app_routes ----------------------------------------------------------------------------


def test_app_routes_fails_on_an_unknown_route_type() -> None:
    from starlette.routing import WebSocketRoute
    from starlette.websockets import WebSocket

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    async def ws(websocket: WebSocket) -> None:
        return None

    app.router.routes.append(WebSocketRoute("/ws", ws))
    with pytest.raises(AssertionError, match="WebSocketRoute"):
        fm.app_routes(app)


def test_app_routes_lists_methods_once_and_mounts() -> None:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    def page() -> None:
        return None

    app.add_api_route("/p/{x}", page, methods=["GET", "POST"])
    assert fm.app_routes(app) == ["GET /p/{x}", "POST /p/{x}"]


# --- context rules -------------------------------------------------------------------------


def test_a_scenario_that_does_not_exist_fails() -> None:
    with pytest.raises(fm.FeatureMapError, match="scenario 'nope'"):
        fm.parse(build(feature("home", HOME, scenario="nope")))


def test_duplicate_names_fail_with_the_name_first() -> None:
    with pytest.raises(fm.FeatureMapError, match="home: duplicate name"):
        fm.parse(build(feature("home", HOME), feature("home", HOME, routes=["GET /x"])))


def test_the_default_checks_do_not_mutate_the_input() -> None:
    data = build(feature("home", HOME))
    before = copy.deepcopy(data)
    fm.parse(data)
    assert data == before
