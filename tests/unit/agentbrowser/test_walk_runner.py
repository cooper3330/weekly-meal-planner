"""The map-driven walk in the runner (#154): fake lifecycle and fake driver, no browser."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import __main__ as cli
from agentbrowser import feature_map, lifecycle, runner
from agentbrowser.evidence import FlowResult, StepResult
from agentbrowser.flow import Flow

HOME = [{"goto": "/"}]
PROFILE = [*HOME, {"click": {"role": "link", "name": "Your profile"}}]
EDIT = [*PROFILE, {"click": {"role": "link", "name": "Edit"}}]
VERSION = [
    *PROFILE,
    {"click": {"role": "link", "name": "History"}},
    {"click": {"role": "link", "name": "Version 1"}},
]


def _feature(name: str, reach: list[dict[str, Any]], routes: list[str], scenario: str) -> Any:
    return {
        "name": name,
        "routes": routes,
        "scenario": scenario,
        "reach": reach,
        "landmarks": [{"role": "heading", "name": name}, {"label": "Servings"}],
        "actions": [],
        "implements": [],
        "flows": [],
        "sources": ["src/mealplanner/web/routes.py"],
        "phone_notes": "",
    }


MAP = {
    "internal": [],
    "features": [
        _feature("profile-edit", EDIT, ["GET /profile/edit"], "profile"),
        _feature("home", HOME, ["GET /"], "empty"),
        _feature("profile", PROFILE, ["GET /profile"], "profile"),
        _feature("profile-version", VERSION, ["GET /profile/history/{version}"], "profile"),
    ],
}
SORTED = ["home", "profile", "profile-edit", "profile-version"]

FLOW = {
    "proves": [{"issue": 1, "criteria": ["AC-1"]}],
    "scenario": "empty",
    "steps": [{"goto": "/"}],
}


class FakeLifecycle:
    def __init__(self, fail_start: str | None = None) -> None:
        self.events: list[tuple[str, str]] = []
        self.starts: list[tuple[str, str, str]] = []
        self.fail_start = fail_start

    def start(self, name: str, scenario: str, mode: str = "demo") -> Any:
        self.events.append(("start", name))
        self.starts.append((name, scenario, mode))
        if self.fail_start is not None and scenario == self.fail_start:
            raise lifecycle.LifecycleError("the app would not start")
        return lifecycle.AppHandle(
            name, "http://127.0.0.1:9", 9, 1, Path("/nonexistent/data"), Path("/nonexistent/log")
        )

    def stop(self, name: str) -> None:
        self.events.append(("stop", name))


class FakeDriver:
    class BrowserMissingError(RuntimeError):
        pass

    def __init__(self) -> None:
        self.failing: dict[str, list[str]] = {}
        self.fail_once: set[str] = set()
        self.raises: str | None = None
        self.calls: list[str] = []
        self.kwargs: dict[str, dict[str, Any]] = {}
        self.flows: dict[str, Flow] = {}
        self.out_dirs: dict[str, Path] = {}
        self.browsers_opened = 0

    @contextmanager
    def open_browser(self) -> Iterator[object]:
        self.browsers_opened += 1
        yield object()

    def run_flow(
        self,
        flow: Flow,
        base_url: str,
        out_dir: Path,
        *,
        name: str | None = None,
        browser: object = None,
        reproduce: str | None = None,
        **extra: Any,
    ) -> FlowResult:
        assert name is not None
        self.calls.append(name)
        self.kwargs[name] = {"reproduce": reproduce, **extra}
        self.flows[name] = flow
        self.out_dirs[name] = out_dir
        if self.raises == name:
            raise RuntimeError("driver crashed")
        rel = extra.get("rel_dir") or name
        result = FlowResult(name, [], reproduce=reproduce)
        result.steps = [StepResult("goto", None, f"{rel}/01-goto.png", True)]
        failures = self.failing.get(name)
        if failures and not (name in self.fail_once and self.calls.count(name) > 1):
            result.failures.extend(failures)
        return result


STEP_FAILURE = (
    "step 2 (click) failed:\n"
    "  expected: a visible, enabled link named 'Your profile' to click within 5 s\n"
    "  actual: not found or not usable (timeout)\n"
    "  the page offered:\n  link: Home"
)


@dataclass
class Env:
    flows: Path
    out: Path
    life: FakeLifecycle
    drv: FakeDriver
    map_path: Path

    def run(self, flow: Path | None = None, feature: str | None = None) -> int:
        return runner.run(
            flow,
            self.out,
            flows_dir=self.flows,
            lifecycle_api=self.life,
            driver_api=self.drv,
            map_path=self.map_path,
            feature=feature,
        )


@pytest.fixture
def env(tmp_path: Path) -> Env:
    path = tmp_path / "feature-map.json"
    path.write_text(json.dumps(MAP), encoding="utf-8")
    return Env(tmp_path / "flows", tmp_path / "out", FakeLifecycle(), FakeDriver(), path)


def _write_flow(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(FLOW))
    return path


# --- visiting ------------------------------------------------------------------------------


def test_the_walk_visits_every_feature_once_in_name_order(env: Env) -> None:
    assert env.run() == 0
    assert env.drv.calls == SORTED


def test_each_feature_gets_its_own_app_with_its_scenario_in_demo_mode(env: Env) -> None:
    assert env.run() == 0
    assert [e[0] for e in env.life.events] == ["start", "stop"] * 4
    names = [e[1] for e in env.life.events[::2]]
    assert len(set(names)) == 4 and all(n.startswith("e2e-") for n in names)
    assert [(s[1], s[2]) for s in env.life.starts] == [
        ("empty", "demo"),
        ("profile", "demo"),
        ("profile", "demo"),
        ("profile", "demo"),
    ]


def test_each_feature_runs_its_walk_flow_with_a_path_check(env: Env) -> None:
    assert env.run() == 0
    parsed = feature_map.parse(MAP)
    for feature in parsed.features:
        assert env.drv.flows[feature.name] == feature_map.to_walk_flow(feature)
        check = env.drv.kwargs[feature.name]["path_check"]
        assert check == feature_map.path_check(feature)
    assert env.drv.kwargs["profile-version"]["path_check"].templates == [
        "/profile/history/{version}"
    ]
    assert env.drv.kwargs["profile-version"]["path_check"].after_step == 4


def test_the_walk_writes_into_walk_slash_feature_and_reproduces_by_feature(env: Env) -> None:
    assert env.run() == 0
    assert env.drv.out_dirs["profile"] == env.out / "walk" / "profile"
    assert env.drv.kwargs["profile"]["rel_dir"] == "walk/profile"
    assert env.drv.kwargs["profile"]["reproduce"] == "make e2e FEATURE=profile"


def test_the_walk_shares_one_browser_with_the_flows_and_runs_after_them(env: Env) -> None:
    _write_flow(env.flows, "a-flow")
    assert env.run() == 0
    assert env.drv.calls == ["a-flow", *SORTED]
    assert env.drv.browsers_opened == 1


def test_with_no_flows_the_walk_still_runs(env: Env) -> None:
    assert not env.flows.exists()
    assert env.run() == 0
    assert env.drv.calls == SORTED


def test_the_app_is_stopped_when_the_driver_raises(env: Env) -> None:
    env.drv.raises = "profile"
    with pytest.raises(RuntimeError, match="driver crashed"):
        env.run()
    assert env.life.events[-1][0] == "stop"
    assert [e[0] for e in env.life.events].count("start") == [e[0] for e in env.life.events].count(
        "stop"
    )


def test_an_app_that_will_not_start_fails_that_feature_only(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.life.fail_start = "empty"  # only the home entry's scenario
    assert env.run() == 1
    err = capsys.readouterr().err
    assert "the app would not start" in err
    assert err.strip().splitlines()[-1] == "Reproduce: make e2e FEATURE=home"
    assert env.drv.calls == SORTED[1:]


# --- selecting -----------------------------------------------------------------------------


def test_feature_option_walks_only_that_feature(env: Env) -> None:
    _write_flow(env.flows, "a-flow")
    assert env.run(feature="profile") == 0
    assert env.drv.calls == ["profile"]
    assert len(env.life.starts) == 1


def test_an_unknown_feature_fails_with_a_message_and_starts_nothing(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    assert env.run(feature="nope") == 2
    err = capsys.readouterr().err
    assert "nope" in err and "profile-edit" in err  # names the known ones
    assert env.life.events == [] and env.drv.browsers_opened == 0


def test_flow_option_runs_no_walk(env: Env) -> None:
    chosen = _write_flow(env.flows, "b")
    assert env.run(chosen) == 0
    assert env.drv.calls == ["b"]


def test_flow_and_feature_together_are_refused(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    chosen = _write_flow(env.flows, "b")
    assert env.run(chosen, feature="profile") == 2
    assert "--flow" in capsys.readouterr().err
    assert env.drv.calls == []


def test_a_map_with_no_features_and_no_flows_prints_no_flows(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.map_path.write_text(json.dumps({"internal": [], "features": []}))
    assert env.run() == 0
    assert "no flows" in capsys.readouterr().out
    assert env.life.events == [] and env.drv.browsers_opened == 0


def test_an_invalid_map_fails_with_the_loader_message(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.map_path.write_text(json.dumps({"internal": [], "features": [{"name": "x"}]}))
    assert env.run() == 1
    assert "x: " in capsys.readouterr().err
    assert env.life.events == []


def test_the_runner_default_map_is_the_repository_map() -> None:
    assert runner.DEFAULT_MAP == feature_map.REPO_ROOT / "tests" / "e2e" / "feature-map.json"


# --- failing -------------------------------------------------------------------------------


def test_a_failing_feature_exits_1_with_the_full_block(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.drv.failing = {"profile": [STEP_FAILURE, "console error: boom"]}
    assert env.run() == 1
    err = capsys.readouterr().err
    assert "FAIL feature profile" in err
    assert "feature profile: step 2 (click) failed" in err
    assert "expected: a visible, enabled link named 'Your profile'" in err
    assert "actual: not found" in err and "link: Home" in err
    assert "console error: boom" in err
    assert str(env.out / "walk" / "profile") in err
    lines = [line for line in err.splitlines() if line.strip()]
    assert lines[-1] == "Reproduce: make e2e FEATURE=profile"
    assert "FAIL feature home" not in err


def test_a_console_error_and_an_external_request_fail_the_walk_naming_the_feature(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.drv.failing = {
        "home": ["console error: boom"],
        "profile-edit": ["external request (aborted): GET http://evil.test/"],
    }
    assert env.run() == 1
    err = capsys.readouterr().err
    assert "feature home: console error: boom" in err
    assert "feature profile-edit: external request (aborted)" in err
    assert "Reproduce: make e2e FEATURE=home" in err
    assert "Reproduce: make e2e FEATURE=profile-edit" in err


def test_a_failing_walk_with_passing_flows_exits_1(env: Env) -> None:
    _write_flow(env.flows, "a-flow")
    env.drv.failing = {"home": ["step 2 (expect) failed:\n  expected: x\n  actual: y"]}
    assert env.run() == 1


def test_a_failing_flow_still_lets_the_walk_run(env: Env) -> None:
    _write_flow(env.flows, "a-flow")
    env.drv.failing = {"a-flow": ["step 1 (goto) failed"]}
    assert env.run() == 1
    assert env.drv.calls == ["a-flow", *SORTED]


def test_nothing_is_retried(env: Env) -> None:
    env.drv.failing = {"profile": [STEP_FAILURE]}
    env.drv.fail_once = {"profile"}  # would pass on a second call
    assert env.run() == 1
    assert env.drv.calls.count("profile") == 1
    assert [e[0] for e in env.life.events].count("start") == 4


# --- evidence ------------------------------------------------------------------------------


def test_summary_json_has_a_walk_record_per_feature(env: Env) -> None:
    env.drv.failing = {"profile": [STEP_FAILURE]}
    assert env.run() == 1
    data = json.loads((env.out / "summary.json").read_text())
    walk = data["walk"]
    assert [w["feature"] for w in walk] == SORTED
    assert {"feature", "status", "neighbours", "steps", "failures"} <= set(walk[0])
    by_name = {w["feature"]: w for w in walk}
    assert by_name["home"]["status"] == "pass"
    assert by_name["profile"]["status"] == "fail"
    assert by_name["profile"]["neighbours"] == ["home", "profile-edit", "profile-version"]
    assert by_name["home"]["neighbours"] == ["profile"]
    assert by_name["profile"]["steps"][0]["screenshot"] == "walk/profile/01-goto.png"
    assert by_name["profile"]["failures"] == [f"feature profile: {STEP_FAILURE}"]
    assert by_name["home"]["failures"] == []
    assert data["flows"] == []
    assert any("feature profile" in f for f in data["failures"])
    assert "make e2e FEATURE=profile" in data["reproduce"]


def test_summary_md_has_a_walk_section_with_one_line_per_feature(env: Env) -> None:
    env.drv.failing = {"profile": [STEP_FAILURE]}
    assert env.run() == 1
    md = (env.out / "summary.md").read_text()
    assert "## Walk" in md
    assert "- home: pass (neighbours: profile)" in md
    assert "- profile: FAIL (neighbours: home, profile-edit, profile-version)" in md
    assert "- profile-version: pass (neighbours: profile)" in md
    assert "Result: FAIL (3/4 features)" in md
    assert "link: Home" in md  # what the page offered
    assert "Reproduce: make e2e FEATURE=profile" in md
    assert "Result: FAIL" in md


def test_a_run_with_no_walk_has_an_empty_walk_list_and_no_walk_section(env: Env) -> None:
    chosen = _write_flow(env.flows, "b")
    assert env.run(chosen) == 0
    data = json.loads((env.out / "summary.json").read_text())
    assert data["walk"] == []
    assert "## Walk" not in (env.out / "summary.md").read_text()


# --- the command line ----------------------------------------------------------------------


def test_cli_passes_feature_and_map_to_the_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(flow: Path | None, out: Path, **kwargs: Any) -> int:
        seen.update(flow=flow, out=out, **kwargs)
        return 0

    monkeypatch.setattr(runner, "run", fake_run)
    assert (
        cli.main(["e2e", "--feature", "profile", "--map", "m.json", "--out", str(tmp_path / "o")])
        == 0
    )
    assert seen["feature"] == "profile" and seen["map_path"] == Path("m.json")
    assert seen["flow"] is None


def test_cli_without_options_passes_no_feature_and_the_default_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(flow: Path | None, out: Path, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(runner, "run", fake_run)
    assert cli.main(["e2e", "--out", str(tmp_path / "o")]) == 0
    assert seen == {"feature": None, "map_path": None}


def test_cli_unknown_feature_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: Env,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runner, "MAP_PATH", env.map_path)
    assert cli.main(["e2e", "--feature", "nope", "--out", str(tmp_path / "o")]) == 2
    assert "nope" in capsys.readouterr().err
