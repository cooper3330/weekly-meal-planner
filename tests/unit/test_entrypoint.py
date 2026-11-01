"""The real ``make run`` invocation must be able to import the package from the repo root."""

import os
import subprocess
import sys
from pathlib import Path

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
