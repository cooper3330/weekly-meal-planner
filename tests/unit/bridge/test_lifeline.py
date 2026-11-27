"""The stdin lifeline: EOF on stdin makes a child terminate itself (#120).

Run as subprocesses: the lifeline sends SIGTERM to its own process.
"""

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[3] / "src")

STUB = """
import sys, time
from {package}.lifeline import start_lifeline
if "--lifeline-stdin" in sys.argv:
    start_lifeline()
print("up", flush=True)
time.sleep(float(sys.argv[1]))
print("finished", flush=True)
"""


def start(tmp_path: Path, package: str, *args: str) -> subprocess.Popen[str]:
    script = tmp_path / f"stub_{package}.py"
    script.write_text(textwrap.dedent(STUB.format(package=package)))
    proc = subprocess.Popen(
        [sys.executable, str(script), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin"},
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "up"
    return proc


@pytest.mark.parametrize("package", ["bridge", "mealplanner"])
def test_lifeline_eof_terminates(tmp_path: Path, package: str) -> None:
    proc = start(tmp_path, package, "60", "--lifeline-stdin")
    try:
        assert proc.poll() is None
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=5)
        assert proc.returncode != 0  # terminated by SIGTERM, did not finish normally
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("package", ["bridge", "mealplanner"])
def test_lifeline_keeps_running_while_pipe_open(tmp_path: Path, package: str) -> None:
    proc = start(tmp_path, package, "1.0", "--lifeline-stdin")
    try:
        time.sleep(0.2)
        assert proc.poll() is None
        proc.wait(timeout=5)  # runs to completion with the pipe still open
        assert proc.returncode == 0
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("package", ["bridge", "mealplanner"])
def test_without_flag_closing_stdin_does_nothing(tmp_path: Path, package: str) -> None:
    proc = start(tmp_path, package, "1.0")
    try:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=5)
        assert proc.returncode == 0
    finally:
        proc.kill()
        proc.wait()
