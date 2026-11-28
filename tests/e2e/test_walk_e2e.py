"""The map-driven walk against the real demo app and Chromium (#154).

Excluded from `make check` (needs Chromium; `make browser-install`). Run with:
`uv run pytest tests/e2e -o addopts=`
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import lifecycle, runner

FIXTURES = Path(__file__).parent / "fixtures"
REAL_MAP = lifecycle.REPO_ROOT / "tests" / "e2e" / "feature-map.json"


class Recording:
    """The real lifecycle, remembering every app it started."""

    def __init__(self) -> None:
        self.handles: list[lifecycle.AppHandle] = []

    def start(self, name: str, scenario: str, mode: str = "demo") -> lifecycle.AppHandle:
        handle = lifecycle.start(name, scenario, mode)
        self.handles.append(handle)
        return handle

    def stop(self, name: str) -> None:
        lifecycle.stop(name)


def _gone(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def scratch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def _walk(out: Path, api: Any, map_path: Path, feature: str | None = None) -> int:
    from agentbrowser import driver

    return runner.run(
        None,
        out,
        flows_dir=out / "no-flows",
        lifecycle_api=api,
        driver_api=driver,
        map_path=map_path,
        feature=feature,
    )


def _nothing_left(api: Recording, scratch: Path) -> None:
    assert api.handles
    assert all(_gone(h.pid) for h in api.handles)
    assert list(scratch.iterdir()) == []
    assert not any(lifecycle.STATE_DIR.glob("e2e-*.json"))


def test_the_real_map_walks_green_and_leaves_nothing_behind(
    scratch: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Recording()
    assert _walk(tmp_path / "out", api, REAL_MAP) == 0
    out = capsys.readouterr().out
    names = (
        "home",
        "plan",
        "grocery",
        "recipes",
        "recipe",
        "profile",
        "profile-edit",
        "profile-history",
        "profile-version",
    )
    for name in names:
        assert f"PASS feature {name}" in out
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert [w["status"] for w in summary["walk"]] == ["passed"] * len(names)
    shots = [s["screenshot"] for w in summary["walk"] for s in w["steps"]]
    assert all(s.startswith("walk/") for s in shots)
    assert all((tmp_path / "out" / s).is_file() for s in shots)
    assert "## Walk" in (tmp_path / "out" / "summary.md").read_text()
    _nothing_left(api, scratch)


def test_one_feature_by_name(scratch: Path, tmp_path: Path) -> None:
    api = Recording()
    assert _walk(tmp_path / "out", api, REAL_MAP, "profile-version") == 0
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert [w["feature"] for w in summary["walk"]] == ["profile-version"]
    assert summary["walk"][0]["neighbours"] == ["profile-history"]
    assert len(api.handles) == 1
    _nothing_left(api, scratch)


def test_a_renamed_link_fails_at_that_step_and_lists_what_the_page_offered(
    scratch: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Recording()
    assert _walk(tmp_path / "out", api, FIXTURES / "walk-broken-reach.json", "profile") == 1
    err = capsys.readouterr().err
    assert "FAIL feature profile" in err
    assert "feature profile: step 2 (click) failed" in err
    assert "link named 'Profil'" in err
    assert "link: Your profile" in err  # what the page really offered
    assert [line for line in err.splitlines() if line.strip()][-1] == (
        "Reproduce: make e2e FEATURE=profile"
    )
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    step = summary["walk"][0]["steps"][1]
    assert {"role": "link", "name": "Your profile"} in step["offered"]
    assert len(step["offered"]) <= 30
    assert "link: Your profile" in (tmp_path / "out" / "summary.md").read_text()
    _nothing_left(api, scratch)


def test_a_reach_that_ends_on_the_wrong_page_fails(
    scratch: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Recording()
    assert _walk(tmp_path / "out", api, FIXTURES / "walk-wrong-page.json", "profile-history") == 1
    err = capsys.readouterr().err
    assert "reached /profile, expected /profile/history" in err
    assert "feature profile-history: step 2 (click) failed" in err
    _nothing_left(api, scratch)


def test_a_missing_landmark_fails_and_names_it(
    scratch: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Recording()
    assert _walk(tmp_path / "out", api, FIXTURES / "walk-missing-landmark.json") == 1
    err = capsys.readouterr().err
    assert "feature home: step 3 (expect) failed" in err
    assert "button named 'Plan my week'" in err
    assert "Reproduce: make e2e FEATURE=home" in err
    _nothing_left(api, scratch)
