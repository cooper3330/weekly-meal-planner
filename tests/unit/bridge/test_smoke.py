"""make bridge-smoke (#117): owner-run check against the real CLI; never in make check."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from bridge.registry import Registry
from bridge.smoke import run_smoke

REPO_ROOT = Path(__file__).resolve().parents[3]

OK_STUB = """
import json, sys
if "--version" in sys.argv:
    print("2.1.300 (stub)")
    sys.exit(0)
sys.stdin.read()
print(json.dumps({"is_error": False, "result": json.dumps({"ok": True})}))
"""


def _smoke(
    tmp_path: Path, reg: Registry, capsys: pytest.CaptureFixture[str], env: dict[str, str]
) -> tuple[int, str]:
    # The smoke check runs the command named "claude"; the fixture's command is "stub".
    reg = Registry(max_concurrent=1, commands={"claude": reg.commands["stub"]})
    code = run_smoke(reg, home=str(tmp_path), parent_env=env)
    return code, capsys.readouterr().out


def test_smoke_passes_and_prints_report(
    tmp_path: Path, stub_registry: Callable[..., Registry], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _smoke(tmp_path, stub_registry(OK_STUB), capsys, {"SECRET": "leak-me"})
    assert code == 0
    assert "2.1.300" in out
    assert "leak-me" not in out


def test_smoke_fails_on_error_envelope(
    tmp_path: Path, stub_registry: Callable[..., Registry], capsys: pytest.CaptureFixture[str]
) -> None:
    body = "import json, sys\nif '--version' in sys.argv: print('1.0'); sys.exit(0)\n"
    body += "print(json.dumps({'is_error': True, 'result': 'Not logged in'}))\nsys.exit(1)\n"
    code, out = _smoke(tmp_path, stub_registry(body), capsys, {})
    assert code == 1
    assert "is_error" in out


def test_smoke_fails_on_unparseable_stdout(
    tmp_path: Path, stub_registry: Callable[..., Registry], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _smoke(tmp_path, stub_registry("print('not json')\n"), capsys, {})
    assert code == 1


def test_smoke_fails_on_wrong_result(
    tmp_path: Path, stub_registry: Callable[..., Registry], capsys: pytest.CaptureFixture[str]
) -> None:
    body = "import json\nprint(json.dumps({'is_error': False, 'result': '{\"ok\": false}'}))\n"
    code, _ = _smoke(tmp_path, stub_registry(body), capsys, {})
    assert code == 1


def test_smoke_missing_program_fails(
    tmp_path: Path, registry_for: Callable[..., Registry], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _smoke(tmp_path, registry_for(tmp_path / "absent"), capsys, {})
    assert code == 1
    assert str(tmp_path) not in out


def test_smoke_not_in_make_check() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert re.search(r"^bridge-smoke:", makefile, re.M)
    match = re.search(r"^check:.*?(?=^\S)", makefile, re.M | re.S)
    assert match is not None
    assert "bridge-smoke" not in match.group(0)
    assert "bridge.smoke" not in match.group(0)
