"""AC-14 of #112: the app has no Anthropic SDK path and cannot read an API key (ADR 0022)."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from bridge.registry import RegistryError, build_child_env, load_registry
from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.llm.cli_bridge_client import CLIBridgeLLMClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TEXT_SUFFIXES = {".py", ".html", ".toml", ".sql", ".json", ".txt"}


def _source_files() -> list[Path]:
    return [
        p
        for p in (SRC / "mealplanner").rglob("*")
        if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts
    ]


def test_no_sdk_references() -> None:
    files = _source_files()
    assert files, "no files found under src/mealplanner (wrong root?)"
    files += [ROOT / "pyproject.toml", ROOT / ".env.example"]
    offenders = [
        str(p.relative_to(ROOT))
        for p in files
        if re.search("anthropic", p.read_text(encoding="utf-8", errors="replace"), re.I)
    ]
    assert offenders == []


def test_bridge_still_strips_api_key(tmp_path: Path) -> None:
    # The supervisor's own stripping is covered by test_supervisor.py::test_secret_not_exposed.
    registry_path = SRC / "bridge" / "commands.toml"
    original = registry_path.read_text()
    line = re.search(r"^passthrough_env = \[.*\]$", original, re.M)
    assert line
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        changed = tmp_path / f"{name}.toml"
        changed.write_text(original.replace(line.group(0), f'passthrough_env = ["HOME", "{name}"]'))
        with pytest.raises(RegistryError, match=name):
            load_registry(changed)
    command = load_registry(registry_path).commands["claude"]
    child = build_child_env(command, {"HOME": "/h", "ANTHROPIC_API_KEY": "x"})
    assert "ANTHROPIC_API_KEY" not in child


def test_uv_lock_has_no_anthropic() -> None:
    assert 'name = "anthropic"' not in (ROOT / "uv.lock").read_text()


def test_app_makes_no_api_client(tmp_path: Path) -> None:
    settings = load_settings({"MEALPLANNER_DATA_DIR": str(tmp_path)})
    assert isinstance(create_app(settings).state.llm_client, CLIBridgeLLMClient)
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r})\n"
        "import mealplanner, mealplanner.app\n"
        "assert not any('anthropic' in m.lower() for m in sys.modules), 'anthropic imported'\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
