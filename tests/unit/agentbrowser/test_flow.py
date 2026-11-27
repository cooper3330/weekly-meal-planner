"""Flow loader (#102, ADR 0028): pure Python, no browser."""

import json
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import flow


def _write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "f.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def _flow(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"steps": [{"goto": "/"}]}
    base.update(over)
    return base


def _fails(tmp_path: Path, data: Any, needle: str, *, strict: bool = False) -> None:
    with pytest.raises(flow.FlowError) as info:
        flow.load(_write(tmp_path, data), strict=strict)
    assert needle in str(info.value)


def test_valid_flow_parses_with_defaults(tmp_path: Path) -> None:
    loaded = flow.load(_write(tmp_path, _flow()))
    assert loaded.mode == "demo"
    assert loaded.scenario == "profile"
    assert loaded.viewport == "360x640"
    assert (loaded.width, loaded.height) == (360, 640)
    assert loaded.proves == []
    assert loaded.steps[0].kind == "goto"


def test_full_flow_from_adr_parses(tmp_path: Path) -> None:
    data = {
        "proves": [{"issue": 99, "criteria": ["AC-4"]}],
        "mode": "wired",
        "scenario": "empty",
        "viewport": "412x915",
        "steps": [
            {"goto": "/"},
            {"click": {"role": "link", "name": "Profile"}},
            {"expect_text": "Your profile"},
            {"expect_no_text": "Oops"},
            {"fill": {"label": "Dislikes", "value": "olives"}},
            {"select": {"label": "Effort", "value": "quick"}},
            {"press": "Enter"},
            {"click": {"text": "Save"}, "shows": "AC-2"},
        ],
    }
    loaded = flow.load(_write(tmp_path, data))
    assert [s.kind for s in loaded.steps] == [
        "goto",
        "click",
        "expect_text",
        "expect_no_text",
        "fill",
        "select",
        "press",
        "click",
    ]
    assert loaded.steps[-1].shows == "AC-2"
    assert loaded.proves[0].issue == 99


def test_unknown_step_kind(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[{"hover": {"text": "x"}}]), "hover")


@pytest.mark.parametrize("key", ["skip", "retries", "xfail"])
def test_unknown_top_level_key(tmp_path: Path, key: str) -> None:
    _fails(tmp_path, _flow(**{key: True}), key)


@pytest.mark.parametrize("path", ["https://x", "//x", "http://localhost/", "profile"])
def test_goto_must_be_a_local_path(tmp_path: Path, path: str) -> None:
    _fails(tmp_path, _flow(steps=[{"goto": path}]), "goto")


def test_css_only_locator_is_rejected(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[{"click": {"css": "#save"}}]), "css")
    _fails(tmp_path, _flow(steps=[{"click": {"selector": "button"}}]), "selector")


def test_locator_needs_exactly_one_form(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[{"click": {}}]), "click")
    _fails(tmp_path, _flow(steps=[{"click": {"role": "button"}}]), "click")
    _fails(tmp_path, _flow(steps=[{"click": {"label": "a", "text": "b"}}]), "click")


def test_fill_and_select_need_a_value_click_must_not_have_one(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[{"fill": {"label": "a"}}]), "fill")
    _fails(tmp_path, _flow(steps=[{"select": {"label": "a"}}]), "select")
    _fails(tmp_path, _flow(steps=[{"click": {"label": "a", "value": "v"}}]), "click")


def test_step_needs_exactly_one_kind(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[{"goto": "/", "press": "Enter"}]), "steps.0")
    _fails(tmp_path, _flow(steps=[{"shows": "AC-1"}]), "steps.0")


@pytest.mark.parametrize("viewport", ["360", "0x640", "360x0", "axb", "360x640x2", "-1x640"])
def test_bad_viewport(tmp_path: Path, viewport: str) -> None:
    _fails(tmp_path, _flow(viewport=viewport), "viewport")


def test_step_limit(tmp_path: Path) -> None:
    flow.load(_write(tmp_path, _flow(steps=[{"goto": "/"}] * 40)))
    _fails(tmp_path, _flow(steps=[{"goto": "/"}] * 41), "steps")


def test_no_steps_is_invalid(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[]), "steps")


@pytest.mark.parametrize("issue", [0, -3, "7", 1.5, None, True])
def test_issue_must_be_a_positive_integer(tmp_path: Path, issue: Any) -> None:
    _fails(tmp_path, _flow(proves=[{"issue": issue, "criteria": ["AC-1"]}]), "issue")


@pytest.mark.parametrize("criterion", ["AC1", "ac-1", "AC-", "AC-1x", "1", ""])
def test_criterion_must_match(tmp_path: Path, criterion: str) -> None:
    _fails(tmp_path, _flow(proves=[{"issue": 1, "criteria": [criterion]}]), "criteria")


def test_bad_shows(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[{"goto": "/", "shows": "nice"}]), "shows")


def test_bad_mode(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(mode="live"), "mode")


def test_strict_requires_proves(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(), "proves", strict=True)
    flow.load(_write(tmp_path, _flow(proves=[{"issue": 1, "criteria": ["AC-1"]}])), strict=True)


def test_not_json_and_missing_file(tmp_path: Path) -> None:
    _fails(tmp_path, "{nope", "JSON")
    with pytest.raises(flow.FlowError):
        flow.load(tmp_path / "absent.json")


def test_top_level_must_be_object(tmp_path: Path) -> None:
    _fails(tmp_path, "[]", "object")
