"""Runner tests (#104): a fake lifecycle and a fake driver, no browser and no app."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import __main__ as cli
from agentbrowser import lifecycle, runner
from agentbrowser.evidence import FlowResult
from agentbrowser.flow import Flow

FLOW = {
    "proves": [{"issue": 1, "criteria": ["AC-1"]}],
    "scenario": "empty",
    "steps": [{"goto": "/"}],
}


def _write(directory: Path, name: str, **overrides: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps({**FLOW, **overrides}))
    return path


class FakeLifecycle:
    def __init__(self, fail_start: bool = False) -> None:
        self.events: list[tuple[str, str]] = []
        self.starts: list[tuple[str, str, str]] = []
        self.fail_start = fail_start

    def start(self, name: str, scenario: str, mode: str = "demo") -> Any:
        self.events.append(("start", name))
        self.starts.append((name, scenario, mode))
        if self.fail_start:
            raise lifecycle.LifecycleError("the app would not start")
        return lifecycle.AppHandle(
            name, "http://127.0.0.1:9", 9, 1, Path("/nonexistent/data"), Path("/nonexistent/log")
        )

    def stop(self, name: str) -> None:
        self.events.append(("stop", name))


class FakeDriver:
    class BrowserMissingError(RuntimeError):
        pass

    def __init__(self, failing: set[str] | None = None, raises: bool = False) -> None:
        self.failing = failing or set()
        self.raises = raises
        self.calls: list[str] = []
        self.browsers_opened = 0
        self.browsers_closed = 0

    @contextmanager
    def open_browser(self) -> Iterator[object]:
        self.browsers_opened += 1
        try:
            yield object()
        finally:
            self.browsers_closed += 1

    def run_flow(
        self,
        flow: Flow,
        base_url: str,
        out_dir: Path,
        *,
        name: str | None = None,
        browser: object = None,
        reproduce: str | None = None,
        budget: object = None,
    ) -> FlowResult:
        assert name is not None
        self.calls.append(name)
        if self.raises:
            raise RuntimeError("driver crashed")
        result = FlowResult(name, [], reproduce=reproduce)
        if name in self.failing:
            result.failures.append(
                "step 2 (expect_text) failed:\n  expected: the text 'x'\n  actual: not found"
            )
            result.failures.append("console error: boom")
            result.failures.append("external request (aborted): GET http://evil.test/")
        return result


@dataclass
class Env:
    flows: Path
    out: Path
    life: FakeLifecycle
    drv: FakeDriver

    def run(self, flow: Path | None = None) -> int:
        return runner.run(
            flow,
            self.out,
            flows_dir=self.flows,
            lifecycle_api=self.life,
            driver_api=self.drv,
        )


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path / "flows", tmp_path / "out", FakeLifecycle(), FakeDriver())


def test_no_flows_prints_no_flows_and_exits_0(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert env.run() == 0
    assert "no flows" in capsys.readouterr().out
    assert env.life.events == []
    assert env.drv.browsers_opened == 0


def test_two_flows_one_failing_prints_the_full_block_and_exits_1(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(env.flows, "a-good")
    _write(env.flows, "b-bad")
    env.drv.failing = {"b-bad"}
    assert env.run() == 1
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "FAIL b-bad" in text
    assert "step 2 (expect_text)" in text
    assert "expected: the text 'x'" in text and "actual: not found" in text
    assert "console error: boom" in text
    assert "external request (aborted)" in text
    assert str(env.out / "b-bad") in text
    assert "a-good" in text
    assert "FAIL a-good" not in text
    lines = [line for line in captured.err.splitlines() if line.strip()]
    assert lines[-1] == f"Reproduce: make e2e FLOW={env.flows / 'b-bad.json'}"
    assert (env.out / "summary.json").exists()


def test_flows_run_in_sorted_order_in_one_shared_browser(env: Env) -> None:
    _write(env.flows, "zeta")
    _write(env.flows, "alpha")
    assert env.run() == 0
    assert env.drv.calls == ["alpha", "zeta"]
    assert env.drv.browsers_opened == 1
    assert env.drv.browsers_closed == 1


def test_each_flow_gets_its_own_app_started_with_its_mode_and_scenario_then_stopped(
    env: Env,
) -> None:
    _write(env.flows, "one", scenario="empty")
    _write(env.flows, "two", scenario="profile")
    assert env.run() == 0
    assert [e[0] for e in env.life.events] == ["start", "stop", "start", "stop"]
    names = [e[1] for e in env.life.events]
    assert names[0] == names[1] and names[2] == names[3] and names[0] != names[2]
    assert [(s[1], s[2]) for s in env.life.starts] == [("empty", "demo"), ("profile", "demo")]


def test_the_app_is_stopped_when_the_driver_raises(env: Env) -> None:
    _write(env.flows, "boom")
    env.drv.raises = True
    with pytest.raises(RuntimeError, match="driver crashed"):
        env.run()
    assert env.life.events[-1][0] == "stop"
    assert env.drv.browsers_closed == 1


def test_the_app_is_stopped_on_ctrl_c(env: Env) -> None:
    _write(env.flows, "interrupted")

    def interrupt(*args: object, **kwargs: object) -> FlowResult:
        raise KeyboardInterrupt

    env.drv.run_flow = interrupt  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        env.run()
    assert env.life.events[-1][0] == "stop"


def test_a_failing_flow_is_not_retried(env: Env) -> None:
    _write(env.flows, "once")
    env.drv.failing = {"once"}
    assert env.run() == 1
    assert env.drv.calls == ["once"]
    assert [e[0] for e in env.life.events] == ["start", "stop"]


def test_an_app_that_will_not_start_fails_that_flow_only(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(env.flows, "a")
    env.life.fail_start = True
    assert env.run() == 1
    err = capsys.readouterr().err
    assert "the app would not start" in err
    assert err.strip().splitlines()[-1].startswith("Reproduce: make e2e FLOW=")
    assert env.drv.calls == []


def test_flow_option_runs_only_that_flow(env: Env) -> None:
    _write(env.flows, "a")
    chosen = _write(env.flows, "b")
    assert env.run(chosen) == 0
    assert env.drv.calls == ["b"]


def test_flow_option_naming_a_missing_file_fails_with_a_message(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = env.flows / "nope.json"
    assert env.run(missing) == 2
    assert f"Flow file not found: {missing}" in capsys.readouterr().err
    assert env.life.events == []


def test_wired_mode_is_rejected_with_the_145_message(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(env.flows, "wired-flow", mode="wired")
    assert env.run() == 1
    err = capsys.readouterr().err
    assert "wired mode arrives in #145" in err
    assert "Reproduce: make e2e FLOW=" in err
    assert env.life.events == []
    assert env.drv.calls == []


def test_an_invalid_flow_file_fails_with_the_loader_message(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.flows.mkdir(parents=True)
    (env.flows / "bad.json").write_text("{not json")
    assert env.run() == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert env.life.events == []


def test_flows_in_the_flows_dir_must_declare_proves(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(env.flows, "unproven", proves=[])
    assert env.run() == 1
    assert "proves: required" in capsys.readouterr().err


def test_a_missing_browser_is_reported_once_and_the_run_fails(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(env.flows, "a")

    @contextmanager
    def missing() -> Iterator[object]:
        raise FakeDriver.BrowserMissingError("Chromium is not installed. Run: make browser-install")
        yield  # pragma: no cover

    env.drv.open_browser = missing  # type: ignore[method-assign]
    assert env.run() == 1
    assert "make browser-install" in capsys.readouterr().err
    assert env.life.events == []


def test_cli_e2e_with_no_flows_exits_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "FLOWS_DIR", tmp_path / "none")
    assert cli.main(["e2e", "--out", str(tmp_path / "out")]) == 0
    assert "no flows" in capsys.readouterr().out


def test_the_summary_records_the_head_commit_from_the_environment(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(env.flows, "a")
    monkeypatch.setenv("AGENTBROWSER_HEAD_SHA", "0123456789abcdef0123456789abcdef01234567")
    assert env.run() == 0
    data = json.loads((env.out / "summary.json").read_text())
    assert data["head_sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert (
        "Head commit: 0123456789abcdef0123456789abcdef01234567"
        in (env.out / "summary.md").read_text()
    )


def test_the_head_commit_is_null_when_the_environment_has_none(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(env.flows, "a")
    monkeypatch.delenv("AGENTBROWSER_HEAD_SHA", raising=False)
    assert env.run() == 0
    assert json.loads((env.out / "summary.json").read_text())["head_sha"] is None


def test_an_empty_head_commit_variable_counts_as_none(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(env.flows, "a")
    monkeypatch.setenv("AGENTBROWSER_HEAD_SHA", "")
    assert env.run() == 0
    assert json.loads((env.out / "summary.json").read_text())["head_sha"] is None
