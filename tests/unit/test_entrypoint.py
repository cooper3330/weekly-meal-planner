"""The real ``make run`` invocation must be able to import the package from the repo root."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_make_run_imports_package_from_repo_root() -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["MEALPLANNER_HOST"] = "0.0.0.0"  # rejected by load_settings, so no server starts
    result = subprocess.run(
        ["make", "run", f"RUN={sys.executable}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "ModuleNotFoundError" not in result.stderr
    assert "ConfigError" in result.stderr
    assert result.returncode != 0


def test_mealplanner_lifeline_flag_starts_lifeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import mealplanner.__main__ as entry

    started: list[bool] = []
    monkeypatch.setattr(entry, "start_lifeline", lambda: started.append(True))
    monkeypatch.setattr(entry, "create_app", lambda settings: None)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setenv("MEALPLANNER_DATA_DIR", str(tmp_path))

    entry.main([])
    assert started == []
    entry.main(["--lifeline-stdin"])
    assert started == [True]


def test_bridge_lifeline_flag_starts_lifeline(monkeypatch: pytest.MonkeyPatch) -> None:
    import bridge.__main__ as entry

    started: list[bool] = []
    monkeypatch.setattr(entry, "start_lifeline", lambda: started.append(True))
    # No port or token: main exits with status 2 right after the lifeline decision.
    for var in ("MEALPLANNER_BRIDGE_PORT", "MEALPLANNER_BRIDGE_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    assert entry.main([]) == 2
    assert started == []
    assert entry.main(["--lifeline-stdin"]) == 2
    assert started == [True]
