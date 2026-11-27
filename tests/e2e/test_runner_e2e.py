"""The runner against the real demo app and Chromium (#104).

Excluded from `make check` (needs Chromium; `make browser-install`). Run with:
`uv run pytest tests/e2e -o addopts=`
"""

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import lifecycle, runner

HOME = lifecycle.REPO_ROOT / "tests" / "flows" / "home.json"
BROKEN = Path(__file__).parent / "fixtures" / "broken-home.json"


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


def test_home_passes_and_a_broken_flow_fails_for_its_reason_and_nothing_is_left(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    from agentbrowser import driver

    api: Any = Recording()
    out = tmp_path / "out"

    assert runner.run(HOME, out / "good", lifecycle_api=api, driver_api=driver) == 0
    captured = capsys.readouterr()
    assert "PASS home" in captured.out

    assert runner.run(BROKEN, out / "bad", lifecycle_api=api, driver_api=driver) == 1
    err = capsys.readouterr().err
    assert "FAIL broken-home" in err
    assert "This text is not on the home page" in err
    assert "expected:" in err and "actual:" in err
    assert str(out / "bad" / "broken-home") in err
    last = [line for line in err.splitlines() if line.strip()][-1]
    assert last == f"Reproduce: make e2e FLOW={BROKEN.relative_to(lifecycle.REPO_ROOT)}"

    assert len(api.handles) == 2
    assert all(_gone(h.pid) for h in api.handles)
    assert list(scratch.iterdir()) == []
    assert not any(lifecycle.STATE_DIR.glob("e2e-*.json"))
