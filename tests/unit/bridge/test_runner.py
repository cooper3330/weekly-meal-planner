"""run_command: no shell, stdin-only input, scratch cwd, minimal env (#117)."""

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from bridge.registry import Registry
from bridge.runner import RunError, run_command

REPO_ROOT = Path(__file__).resolve().parents[3]
HOME = os.path.expanduser("~")


def test_runs_with_stdin_and_returns_stdout(stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry("import sys\nsys.stdout.write(sys.stdin.read().upper())\n")
    res = run_command(reg.commands["stub"], "héllo", home=HOME, parent_env={})
    assert res.exit_code == 0
    assert res.stdout == "HÉLLO"
    assert res.duration_ms >= 0


def test_invalid_utf8_stdout_is_replaced(stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry("import sys\nsys.stdout.buffer.write(b'a\\xffb')\n")
    res = run_command(reg.commands["stub"], "", home=HOME, parent_env={})
    assert res.stdout == "a�b"


def test_nonzero_exit_is_a_result(stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry("import sys\nprint('{\"is_error\": true}')\nsys.exit(3)\n")
    res = run_command(reg.commands["stub"], "x", home=HOME, parent_env={})
    assert res.exit_code == 3
    assert json.loads(res.stdout) == {"is_error": True}


def test_child_exits_without_reading_stdin(stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry("print('done')\n", max_input_bytes=5_000_000)
    res = run_command(reg.commands["stub"], "x" * 2_000_000, home=HOME, parent_env={})
    assert res.stdout.strip() == "done"


def test_child_env_is_fixed_plus_passthrough(
    tmp_path: Path, stub_registry: Callable[..., Registry]
) -> None:
    out = tmp_path / "env.json"
    reg = stub_registry(
        # Python itself may add LC_CTYPE (PEP 538); that is the stub's interpreter, not the bridge.
        "import json, os\n"
        f"env = {{k: v for k, v in os.environ.items() if k != 'LC_CTYPE'}}\n"
        f"json.dump(env, open({str(out)!r}, 'w'))\n",
        env={"FIXED": "1"},
        passthrough=["HOME", "LANG"],
    )
    parent = {
        "HOME": "/h",
        "ANTHROPIC_API_KEY": "sk-secret",
        "MEALPLANNER_BRIDGE_TOKEN": "tok",
        "SECRET_X": "x",
    }
    run_command(reg.commands["stub"], "x", home="/h", parent_env=parent)
    assert json.loads(out.read_text()) == {"FIXED": "1", "HOME": "/h"}


def test_cwd_is_empty_temp_dir_outside_repo(
    tmp_path: Path, stub_registry: Callable[..., Registry]
) -> None:
    out = tmp_path / "cwd.json"
    reg = stub_registry(
        f"import json, os\njson.dump([os.getcwd(), os.listdir('.')], open({str(out)!r}, 'w'))\n"
    )
    run_command(reg.commands["stub"], "x", home=HOME, parent_env={})
    cwd, listing = json.loads(out.read_text())
    assert listing == []
    assert not Path(cwd).resolve().is_relative_to(REPO_ROOT)
    assert Path(cwd).name.startswith("mealplanner-bridge-")
    assert not Path(cwd).exists()


def test_scratch_removed_on_nonzero_exit(
    tmp_path: Path, stub_registry: Callable[..., Registry]
) -> None:
    out = tmp_path / "cwd.txt"
    reg = stub_registry(
        f"import os, sys\nopen({str(out)!r}, 'w').write(os.getcwd())\nsys.exit(1)\n"
    )
    res = run_command(reg.commands["stub"], "x", home=HOME, parent_env={})
    assert res.exit_code == 1
    assert not Path(out.read_text()).exists()


def test_args_passed_as_list_without_shell(
    tmp_path: Path, stub_registry: Callable[..., Registry]
) -> None:
    out = tmp_path / "argv.json"
    reg = stub_registry(
        f"import json, sys\njson.dump(sys.argv[1:], open({str(out)!r}, 'w'))\n",
        args=["-p", "a b; touch pwned", "*"],
    )
    run_command(reg.commands["stub"], "x", home=HOME, parent_env={})
    assert json.loads(out.read_text()) == ["-p", "a b; touch pwned", "*"]


def test_program_missing(tmp_path: Path, stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry("pass\n")
    (tmp_path / "stub.py").unlink()
    with pytest.raises(RunError) as exc:
        run_command(reg.commands["stub"], "x", home=HOME, parent_env={})
    assert (exc.value.status, exc.value.kind) == (503, "program_not_found")


def test_program_not_executable(tmp_path: Path, stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry("pass\n")
    p = tmp_path / "stub.py"
    p.chmod(p.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    with pytest.raises(RunError) as exc:
        run_command(reg.commands["stub"], "x", home=HOME, parent_env={})
    assert exc.value.kind == "program_not_found"


def test_spawn_failure_is_fixed_500(tmp_path: Path, registry_for: Callable[..., Registry]) -> None:
    prog = tmp_path / "bad.sh"
    prog.write_text("#!/nonexistent/interpreter-secret-name\n")
    prog.chmod(0o755)
    reg = registry_for(prog)
    with pytest.raises(RunError) as exc:
        run_command(reg.commands["stub"], "x", home=HOME, parent_env={})
    assert (exc.value.status, exc.value.kind) == (500, "spawn_failed")
    assert "interpreter-secret-name" not in str(exc.value)
