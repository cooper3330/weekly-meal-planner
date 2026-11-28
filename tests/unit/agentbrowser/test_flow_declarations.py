"""Flows declare what they prove; skip, xfail and retry cannot be expressed (#144)."""

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import __main__ as cli
from agentbrowser import flow, runner
from agentbrowser.evidence import FlowResult, write_summary

OK = [{"issue": 99, "criteria": ["AC-4"]}]


def _write(tmp_path: Path, name: str = "f", **over: Any) -> Path:
    data: dict[str, Any] = {"proves": OK, "steps": [{"goto": "/"}]}
    data.update(over)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _fails(path: Path, *needles: str) -> None:
    with pytest.raises(flow.FlowError) as info:
        flow.load(path, strict=True)
    for needle in (path.name, *needles):
        assert needle in str(info.value)


def test_missing_proves(tmp_path: Path) -> None:
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"steps": [{"goto": "/"}]}))
    _fails(path, "proves")


def test_empty_proves(tmp_path: Path) -> None:
    _fails(_write(tmp_path, proves=[]), "proves")


def test_issue_zero(tmp_path: Path) -> None:
    _fails(_write(tmp_path, proves=[{"issue": 0, "criteria": ["AC-1"]}]), "proves.0.issue")


def test_criterion_must_be_an_ac(tmp_path: Path) -> None:
    _fails(_write(tmp_path, proves=[{"issue": 1, "criteria": ["FR-1"]}]), "proves.0.criteria")


def test_empty_criteria(tmp_path: Path) -> None:
    _fails(_write(tmp_path, proves=[{"issue": 1, "criteria": []}]), "proves.0.criteria")


@pytest.mark.parametrize(
    ("key", "value"),
    [("skip", True), ("xfail", True), ("retries", 2), ("only", True), ("flaky", True)],
)
def test_skip_xfail_and_retry_cannot_be_expressed(tmp_path: Path, key: str, value: Any) -> None:
    _fails(_write(tmp_path, **{key: value}), key)


def test_a_step_cannot_carry_skip_either(tmp_path: Path) -> None:
    _fails(_write(tmp_path, steps=[{"goto": "/", "skip": True}]), "skip")


def test_shows_must_be_a_declared_criterion(tmp_path: Path) -> None:
    steps = [{"goto": "/", "shows": "AC-5"}]
    _fails(_write(tmp_path, steps=steps), "step 1", "AC-5", "AC-4")
    flow.load(_write(tmp_path, steps=[{"goto": "/", "shows": "AC-4"}]), strict=True)


def test_shows_is_rejected_in_an_ad_hoc_file_without_proves(tmp_path: Path) -> None:
    path = tmp_path / "adhoc.json"
    path.write_text(json.dumps({"steps": [{"goto": "/"}, {"goto": "/", "shows": "AC-9"}]}))
    with pytest.raises(flow.FlowError) as info:
        flow.load(path)
    assert "step 2 shows AC-9" in str(info.value)


def _flows(tmp_path: Path) -> dict[str, flow.Flow]:
    return {
        "home": flow.load(_write(tmp_path, "home"), strict=True),
        "plan": flow.load(
            _write(
                tmp_path,
                "plan",
                proves=[
                    {"issue": 99, "criteria": ["AC-4", "AC-4", "AC-10"]},
                    {"issue": 7, "criteria": ["AC-1"]},
                ],
            ),
            strict=True,
        ),
    }


def test_coverage_lists_every_flow_once_per_criterion(tmp_path: Path) -> None:
    entries = flow.coverage(_flows(tmp_path))
    assert flow.coverage_lines(entries) == [
        "#7 AC-1: plan",
        "#99 AC-4: home, plan",
        "#99 AC-10: plan",
    ]


def test_e2e_prints_coverage_before_running_and_writes_it_to_the_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    _write(flows, "home")
    order: list[str] = []

    class Life:
        def start(self, *a: Any, **k: Any) -> Any:
            order.append("start")
            raise runner.lifecycle.LifecycleError("no app")

        def stop(self, name: str) -> None:
            pass

    class Drv:
        class BrowserMissingError(Exception):
            pass

        def open_browser(self) -> Any:
            from contextlib import nullcontext

            return nullcontext(object())

    out = tmp_path / "out"
    runner.run(None, out, flows_dir=flows, lifecycle_api=Life(), driver_api=Drv())
    assert "#99 AC-4: home" in capsys.readouterr().out
    data = json.loads((out / "summary.json").read_text())
    assert data["coverage"] == [{"issue": 99, "criterion": "AC-4", "flows": ["home"]}]


def test_summary_coverage_is_the_declared_list(tmp_path: Path) -> None:
    declared: list[dict[str, object]] = [{"issue": 1, "criterion": "AC-1", "flows": ["a"]}]
    write_summary(tmp_path, [FlowResult("a", [])], coverage=declared)
    assert json.loads((tmp_path / "summary.json").read_text())["coverage"] == declared


def _options(parser: argparse.ArgumentParser) -> set[str]:
    found = set(parser._option_string_actions)  # noqa: SLF001
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for sub in action.choices.values():
                found |= _options(sub)
    return found


def test_the_command_line_has_no_skip_xfail_or_retry_options() -> None:
    options = _options(cli.build_parser())
    assert "--flow" in options
    banned = ("skip", "xfail", "retr", "only", "flaky", "rerun")
    assert [o for o in options if any(b in o for b in banned)] == []
