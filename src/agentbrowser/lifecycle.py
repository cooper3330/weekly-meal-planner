"""Start and stop the demo app for agents and e2e flows (ADR 0028).

``start`` picks a free loopback port, makes a temporary data directory, runs the app in its own
process group and waits for ``/healthz``. ``stop`` signals only a process group that this module
started (the recorded pid is its own group leader and its command line names the recorded data
directory), then removes the directory and the state file.
"""

import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mealplanner.demo import DEMO_OWNER

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = REPO_ROOT / ".agent-tmp" / "app"
LOOPBACK = "127.0.0.1"
HEALTH_TIMEOUT = 20.0
GRACE = 5.0
PORT_ATTEMPTS = 3
DIR_PREFIX = "agentbrowser-app-"
LOG_NAME = "app.log"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

CommandFactory = Callable[[int, Path, str], list[str]]


class LifecycleError(RuntimeError):
    """The app could not be started, or a name is already in use."""


@dataclass(frozen=True)
class AppHandle:
    name: str
    url: str
    port: int
    pid: int
    data_dir: Path
    log: Path


def demo_command(port: int, data_dir: Path, scenario: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mealplanner.demo",
        "--port",
        str(port),
        "--data-dir",
        str(data_dir),
        "--scenario",
        scenario,
    ]


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise LifecycleError(f"Invalid app name {name!r}: use letters, digits, '-' and '_'.")


def _state_path(state_dir: Path, name: str) -> Path:
    return state_dir / f"{name}.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return int(s.getsockname()[1])


def _command_line(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def _reap(pid: int) -> None:
    """Collect a finished child so it does not linger as a zombie; no-op for other processes."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_ours(state: dict[str, object]) -> bool:
    """True when the recorded pid is a live group leader whose command names our data dir."""
    pid = state.get("pid")
    data_dir = state.get("data_dir")
    if not isinstance(pid, int) or pid <= 1 or not isinstance(data_dir, str) or not data_dir:
        return False
    _reap(pid)
    try:
        if os.getpgid(pid) != pid:
            return False
    except ProcessLookupError:
        return False
    return data_dir in _command_line(pid)


def _read_state(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _remove_data_dir(path: object) -> None:
    # Only directories this module created are removed, whatever a state file says.
    if isinstance(path, str) and Path(path).name.startswith(DIR_PREFIX):
        shutil.rmtree(path, ignore_errors=True)


def _kill_group(pid: int, grace: float) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        _reap(pid)
        if not _group_exists(pid):
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    for _ in range(100):
        _reap(pid)
        if not _group_exists(pid):
            return
        time.sleep(0.05)


def _healthy(port: int) -> bool:
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=1)
    try:
        conn.request("GET", "/healthz", headers={"Tailscale-User-Login": DEMO_OWNER})
        return conn.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _log_tail(log: Path, lines: int = 40) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class _Failed(Exception):
    def __init__(self, reason: str, *, port_lost: bool = False) -> None:
        super().__init__(reason)
        self.port_lost = port_lost


def _attempt(command: list[str], port: int, log: Path, timeout: float) -> int:
    """Run ``command`` until it answers ``/healthz``; return its pid or raise ``_Failed``."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    with log.open("ab") as out:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            lost = "address already in use" in _log_tail(log).lower()
            raise _Failed(f"The app exited with status {code} before it was ready.", port_lost=lost)
        # Our own process must still be alive after the 200, so a stranger on the port
        # cannot make a dead app look healthy.
        if _healthy(port) and proc.poll() is None:
            return proc.pid
        time.sleep(0.1)
    _kill_group(proc.pid, 2.0)
    raise _Failed(f"The app did not answer /healthz within {timeout:g} s.")


def start(
    name: str,
    scenario: str,
    mode: str = "demo",
    *,
    command: CommandFactory | None = None,
    state_dir: Path | None = None,
    timeout: float = HEALTH_TIMEOUT,
) -> AppHandle:
    """Start the app as ``name`` and return its handle. Raises ``LifecycleError`` on failure."""
    _check_name(name)
    if mode != "demo":
        raise LifecycleError(f"Unsupported mode {mode!r}; only 'demo' is available.")
    make_command = command or demo_command
    state_root = state_dir or STATE_DIR
    state_file = _state_path(state_root, name)
    if state_file.exists():
        existing = _read_state(state_file)
        if existing is not None and _is_ours(existing):
            raise LifecycleError(f"App {name!r} is already up. Run: make app-down APP_NAME={name}")
        # Stale: the process is gone.
        _remove_data_dir(existing.get("data_dir") if existing else None)
        state_file.unlink(missing_ok=True)

    data_dir = Path(tempfile.mkdtemp(prefix=DIR_PREFIX))
    log = data_dir / LOG_NAME
    reason = ""
    for _ in range(PORT_ATTEMPTS):
        port = _free_port()
        try:
            pid = _attempt(make_command(port, data_dir, scenario), port, log, timeout)
        except _Failed as exc:
            reason = str(exc)
            if exc.port_lost:
                continue
            break
        state_root.mkdir(parents=True, exist_ok=True)
        handle = AppHandle(name, f"http://{LOOPBACK}:{port}", port, pid, data_dir, log)
        state = {
            "url": handle.url,
            "port": port,
            "pid": pid,
            "data_dir": str(data_dir),
            "log": str(log),
        }
        state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        return handle
    tail = _log_tail(log)
    shutil.rmtree(data_dir, ignore_errors=True)
    raise LifecycleError(f"{reason}\nLast log lines:\n{tail}")


def stop(name: str, *, state_dir: Path | None = None, grace: float = GRACE) -> None:
    """Stop ``name``. Returns cleanly when it is not up or already gone."""
    _check_name(name)
    state_file = _state_path(state_dir or STATE_DIR, name)
    if not state_file.exists():
        return
    state = _read_state(state_file)
    if state is not None:
        if _is_ours(state):
            pid = state["pid"]
            assert isinstance(pid, int)
            _kill_group(pid, grace)
        _remove_data_dir(state.get("data_dir"))
    state_file.unlink(missing_ok=True)
