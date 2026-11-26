"""#105: the browser library is a dev-only dependency; the app and `make check` need no browser."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:  # type: ignore[type-arg]
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _names(requirements: list[str]) -> set[str]:
    return {
        r.split(";")[0].split("[")[0].split("=")[0].split("<")[0].split(">")[0].strip().lower()
        for r in requirements
    }


def test_playwright_is_in_the_dev_group() -> None:
    assert "playwright" in _names(_pyproject()["dependency-groups"]["dev"])


def test_playwright_is_not_a_runtime_dependency() -> None:
    assert "playwright" not in _names(_pyproject()["project"]["dependencies"])


def test_lock_pins_playwright() -> None:
    assert 'name = "playwright"' in (ROOT / "uv.lock").read_text()
