"""The runner's budget and per-screenshot rule across flows and the walk (#173, ADR 0030).

A fake lifecycle and a fake driver whose flows take screenshots of chosen sizes through the run's
budget, and a fake clock. No browser and no app.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import lifecycle, runner
from agentbrowser.evidence import Budget, FlowResult, StepResult
from agentbrowser.flow import Flow

LIMIT = 1_048_576
FLOW = {
    "proves": [{"issue": 1, "criteria": ["AC-1"]}],
    "scenario": "empty",
    "steps": [{"goto": "/"}],
}


def _feature(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "routes": [f"GET /{name}"],
        "scenario": "empty",
        "reach": [{"goto": "/"}],
        "landmarks": [{"role": "heading", "name": name}],
        "actions": [],
        "implements": [],
        "flows": [],
        "sources": ["src/mealplanner/web/routes.py"],
        "phone_notes": "",
    }


class FakeLifecycle:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def start(self, name: str, scenario: str, mode: str = "demo") -> Any:
        self.events.append(("start", name))
        return lifecycle.AppHandle(
            name, "http://127.0.0.1:9", 9, 1, Path("/nonexistent/data"), Path("/nonexistent/log")
        )

    def stop(self, name: str) -> None:
        self.events.append(("stop", name))


class ShotDriver:
    """Each flow takes screenshots of the sizes given for it, like the real driver does."""

    class BrowserMissingError(RuntimeError):
        pass

    def __init__(self, sizes: dict[str, list[int]], clock: list[float] | None = None) -> None:
        self.sizes = sizes
        self.clock = clock
        self.calls: list[str] = []

    @contextmanager
    def open_browser(self) -> Iterator[object]:
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
        rel_dir: str | None = None,
        path_check: object = None,
        budget: Budget,
    ) -> FlowResult:
        assert name is not None
        self.calls.append(name)
        rel = rel_dir or name
        result = FlowResult(name, [], reproduce=reproduce)
        for number, size in enumerate(self.sizes.get(name, [10]), 1):
            if budget.check() is not None:
                result.failures.append(f"step {number} not run: cut off: {budget.cut_off} reached")
                break
            if self.clock is not None:
                self.clock[0] += 100.0  # each screenshot takes 100 s of fake time
            path = f"{rel}/{number:02d}-goto.png"
            verdict = budget.admit(size)
            if verdict == "oversized":
                result.oversized.append({"path": path, "bytes": size})
                result.failures.append(f"screenshot {path} is {size} bytes, over the 1 MB limit")
                result.steps.append(StepResult("goto", None, None, False, "too big"))
                break
            result.steps.append(StepResult("goto", None, None if verdict else path, True))
            if verdict:
                break
        return result


class Setup:
    def __init__(self, tmp_path: Path, flows: list[str], features: list[str]) -> None:
        self.flows = tmp_path / "flows"
        self.flows.mkdir()
        for name in flows:
            (self.flows / f"{name}.json").write_text(json.dumps(FLOW))
        self.map = tmp_path / "map.json"
        self.map.write_text(
            json.dumps({"internal": [], "features": [_feature(n) for n in features]})
        )
        self.out = tmp_path / "out"
        self.life = FakeLifecycle()

    def run(self, driver: ShotDriver, budget: Budget | None = None) -> int:
        return runner.run(
            None,
            self.out,
            flows_dir=self.flows,
            lifecycle_api=self.life,
            driver_api=driver,
            map_path=self.map,
            budget=budget,
        )

    def summary(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.out / "summary.json").read_text())
        return data


def _stops_match_starts(life: FakeLifecycle) -> None:
    starts = [n for e, n in life.events if e == "start"]
    stops = [n for e, n in life.events if e == "stop"]
    assert starts and sorted(starts) == sorted(stops)


def test_a_run_under_every_limit_has_no_cut_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup = Setup(tmp_path, ["a", "b"], ["home"])
    assert setup.run(ShotDriver({})) == 0
    data = setup.summary()
    assert "cut_off" not in data and data["oversized"] == []
    assert "cut off" not in (setup.out / "summary.md").read_text()
    assert "cut off" not in capsys.readouterr().err


def test_the_time_limit_stops_before_the_next_flow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = [0.0]
    budget = Budget(clock=lambda: clock[0])
    driver = ShotDriver({"a": [10] * 3, "b": [10] * 3}, clock)  # 300 s each
    setup = Setup(tmp_path, ["a", "b", "c"], ["home"])
    assert setup.run(driver, budget) == 1
    assert driver.calls == ["a", "b"]  # c never starts: 600 s have passed
    data = setup.summary()
    assert data["cut_off"] == {"limit": "time", "after_flows": 2}
    assert [f["name"] for f in data["flows"]] == ["a", "b"]
    assert "cut off: time reached after 2 flows" in (setup.out / "summary.md").read_text()
    assert "cut off: time reached after 2 flows" in capsys.readouterr().err
    _stops_match_starts(setup.life)


def test_the_time_limit_before_the_walk_skips_every_feature(tmp_path: Path) -> None:
    clock = [0.0]
    budget = Budget(clock=lambda: clock[0])
    driver = ShotDriver({"a": [10] * 6}, clock)  # 600 s in one flow
    setup = Setup(tmp_path, ["a"], ["home"])
    assert setup.run(driver, budget) == 1
    assert driver.calls == ["a"]
    assert setup.summary()["cut_off"] == {"limit": "time", "after_flows": 1}
    assert setup.summary()["walk"] == []


def test_the_201st_screenshot_is_never_taken(tmp_path: Path) -> None:
    driver = ShotDriver({"a": [10] * 150, "b": [10] * 150})
    setup = Setup(tmp_path, ["a", "b", "c"], [])
    assert setup.run(driver) == 1
    data = setup.summary()
    assert data["cut_off"] == {"limit": "screenshots", "after_flows": 2}
    taken = [s for f in data["flows"] for s in f["steps"] if s["screenshot"]]
    assert len(taken) == 200
    assert driver.calls == ["a", "b"]
    _stops_match_starts(setup.life)


def test_screenshots_totalling_exactly_30_mb_pass_and_the_next_is_not_written(
    tmp_path: Path,
) -> None:
    setup = Setup(tmp_path, ["a", "b"], [])
    assert setup.run(ShotDriver({"a": [LIMIT] * 30, "b": [1]})) == 1  # b's one byte would cross
    data = setup.summary()
    assert data["cut_off"] == {"limit": "bytes", "after_flows": 2}
    assert all(s["screenshot"] for s in data["flows"][0]["steps"])
    assert len(data["flows"][0]["steps"]) == 30
    assert data["flows"][1]["steps"][0]["screenshot"] is None


def test_exactly_30_mb_alone_is_not_a_cut_off(tmp_path: Path) -> None:
    setup = Setup(tmp_path, ["a"], [])
    assert setup.run(ShotDriver({"a": [LIMIT] * 30})) == 0
    assert "cut_off" not in setup.summary()


def test_a_screenshot_that_would_cross_30_mb_is_not_written(tmp_path: Path) -> None:
    setup = Setup(tmp_path, ["a"], [])
    assert setup.run(ShotDriver({"a": [LIMIT] * 31})) == 1  # 30 fit, the 31st is over
    data = setup.summary()
    assert data["cut_off"]["limit"] == "bytes"
    steps = data["flows"][0]["steps"]
    assert len(steps) == 31 and steps[-1]["screenshot"] is None
    assert all(s["screenshot"] for s in steps[:30])


def test_walk_screenshots_count_toward_the_screenshot_budget(tmp_path: Path) -> None:
    sizes = {"a": [10] * 199, "home": [10, 10]}
    setup = Setup(tmp_path, ["a"], ["home", "profile"])
    assert setup.run(ShotDriver(sizes)) == 1
    data = setup.summary()
    assert data["cut_off"] == {"limit": "screenshots", "after_flows": 2}
    assert [w["feature"] for w in data["walk"]] == ["home"]
    assert data["walk"][0]["steps"][0]["screenshot"] == "walk/home/01-goto.png"
    assert len(data["walk"][0]["steps"]) == 1  # the second step never started


def test_walk_screenshots_count_toward_the_byte_budget(tmp_path: Path) -> None:
    sizes = {"a": [LIMIT] * 29 + [LIMIT - 50], "home": [100]}
    setup = Setup(tmp_path, ["a"], ["home"])
    assert setup.run(ShotDriver(sizes)) == 1
    data = setup.summary()
    assert data["cut_off"] == {"limit": "bytes", "after_flows": 2}
    assert data["walk"][0]["steps"][0]["screenshot"] is None
    _stops_match_starts(setup.life)


def test_an_oversized_screenshot_fails_its_flow_and_the_next_flow_runs(
    tmp_path: Path,
) -> None:
    driver = ShotDriver({"a": [LIMIT + 1], "b": [10]})
    setup = Setup(tmp_path, ["a", "b"], [])
    assert setup.run(driver) == 1
    assert driver.calls == ["a", "b"]
    data = setup.summary()
    assert "cut_off" not in data
    assert data["oversized"] == [{"path": "a/01-goto.png", "bytes": LIMIT + 1}]
    assert [f["status"] for f in data["flows"]] == ["failed", "passed"]
    assert data["flows"][0]["steps"][0]["screenshot"] is None


def test_an_oversized_walk_screenshot_is_recorded_and_the_walk_goes_on(tmp_path: Path) -> None:
    driver = ShotDriver({"home": [LIMIT + 1], "profile": [10]})
    setup = Setup(tmp_path, [], ["home", "profile"])
    assert setup.run(driver) == 1
    data = setup.summary()
    assert data["oversized"] == [{"path": "walk/home/01-goto.png", "bytes": LIMIT + 1}]
    assert [w["status"] for w in data["walk"]] == ["failed", "passed"]


def test_a_failed_flow_without_a_driver_result_still_has_a_viewport(tmp_path: Path) -> None:
    setup = Setup(tmp_path, ["a"], [])
    (setup.flows / "bad_name.json").write_text(json.dumps(FLOW))
    assert setup.run(ShotDriver({})) == 1
    data = setup.summary()
    assert {f["viewport"] for f in data["flows"]} == {"360x640"}
    assert data["viewport"] == "360x640"
