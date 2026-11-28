"""`shows` and flow file names (#173, ADR 0030 changes 1 and 5). Pure Python, no browser."""

import json
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import flow

PROVES = {"proves": [{"issue": 1, "criteria": ["AC-2"]}]}
PATTERN = "^[a-z0-9][a-z0-9-]{0,63}$"


def _flow(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"steps": [{"goto": "/"}]}
    base.update(over)
    return base


def _write(tmp_path: Path, data: Any, name: str = "f.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _fails(tmp_path: Path, data: Any, needle: str) -> None:
    with pytest.raises(flow.FlowError) as info:
        flow.load(_write(tmp_path, data))
    assert needle in str(info.value)


def test_shows_among_the_proved_criteria_is_valid(tmp_path: Path) -> None:
    data = _flow(**PROVES, steps=[{"goto": "/", "shows": "AC-2"}])
    assert flow.load(_write(tmp_path, data)).steps[0].shows == "AC-2"


def test_shows_not_in_proves_is_rejected_naming_the_step(tmp_path: Path) -> None:
    data = _flow(**PROVES, steps=[{"goto": "/"}, {"goto": "/", "shows": "AC-3"}])
    _fails(tmp_path, data, "step 2 shows AC-3")


def test_shows_without_proves_is_rejected(tmp_path: Path) -> None:
    _fails(tmp_path, _flow(steps=[{"goto": "/", "shows": "AC-2"}]), "step 1 shows AC-2")


@pytest.mark.parametrize("shows", ["ac-2", "AC-", "AC-2x", "2"])
def test_shows_must_match_the_criterion_pattern(tmp_path: Path, shows: str) -> None:
    _fails(tmp_path, _flow(**PROVES, steps=[{"goto": "/", "shows": shows}]), "shows")


@pytest.mark.parametrize("name", ["plan-week.json", "a.json", "a" * 64 + ".json", "0.json"])
def test_flow_file_names_that_match_load(tmp_path: Path, name: str) -> None:
    assert flow.load(_write(tmp_path, _flow(), name)).steps


@pytest.mark.parametrize(
    "name",
    [
        "plan_week.json",
        "PlanWeek.json",
        "-plan.json",
        "plan week.json",
        "a" * 65 + ".json",
        "walk.json",
        ".json",
    ],
)
def test_flow_file_names_that_do_not_match_are_rejected_naming_the_file(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(flow.FlowError) as info:
        flow.load(_write(tmp_path, _flow(), name))
    assert name in str(info.value)
    assert PATTERN in str(info.value)
