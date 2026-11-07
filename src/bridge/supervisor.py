"""`python -m bridge.supervisor`, what `make run` starts (#120, #112 FR-2, AC-1).

Starts the bridge, waits until it is healthy, starts the app, and stops both together on every
exit path. The port and the token are chosen per run and reach the children through their
environment only: they are never printed, logged, written to a file, or put on a command line.
"""

import http.client
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from types import FrameType

from bridge.config import HOST_VAR, PORT_VAR, TOKEN_VAR

HOST = "127.0.0.1"
HEALTH_TIMEOUT = 15.0
STOP_GRACE = 5.0
POLL_INTERVAL = 0.1
WATCH_INTERVAL = 0.05
SIGNALS = tuple(s for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None)) if s)

Probe = Callable[[], bool]

# The real commands `main()` wires up (module-level so tests can point them at stubs).
BRIDGE_ARGV = [sys.executable, "-m", "bridge", "--lifeline-stdin"]
APP_ARGV = [sys.executable, "-m", "mealplanner", "--lifeline-stdin"]


def pick_free_port() -> int:
    """Bind 127.0.0.1:0 to learn a free port, then release it for the bridge."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def new_token() -> str:
    return secrets.token_urlsafe(32)


def make_probe(host: str, port: int, token: str) -> Probe:
    """A health probe: True when `GET /healthz` with the token answers 200."""

    def probe() -> bool:
        conn = http.client.HTTPConnection(host, port, timeout=1.0)
        try:
            conn.request("GET", "/healthz", headers={"Authorization": f"Bearer {token}"})
            return conn.getresponse().status == 200
        except (OSError, http.client.HTTPException):
            return False
        finally:
            conn.close()

    return probe


def _exit_status(returncode: int) -> int:
    """A non-zero status: an unexpected exit is a failure, even a clean one."""
    if returncode < 0:
        return 128 - returncode  # killed by a signal
    return returncode or 1


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_all(procs: list["subprocess.Popen[bytes]"], stop_grace: float) -> None:
    """SIGTERM each process group, wait up to `stop_grace`, then SIGKILL what is left."""
    # A child's process group id is its pid (start_new_session=True).
    groups = [p.pid for p in procs]
    for pgid in groups:
        _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + stop_grace
    while time.monotonic() < deadline:
        for proc in procs:
            proc.poll()  # reap the leader so its group can empty
        if not any(_group_alive(g) for g in groups):
            break
        time.sleep(0.02)
    for pgid in groups:
        _signal_group(pgid, signal.SIGKILL)
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - nothing more can be done for an unkillable child
            pass
        for stream in (proc.stdin,):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def supervise(
    bridge_argv: Sequence[str],
    app_argv: Sequence[str],
    env: Mapping[str, str],
    probe: Probe,
    *,
    health_timeout: float = HEALTH_TIMEOUT,
    stop_grace: float = STOP_GRACE,
) -> int:
    """Run the bridge, wait for health, run the app; stop both together. Returns the exit status."""
    received: list[int] = []

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        received.append(signum)

    previous: dict[int, object] = {}
    for sig in SIGNALS:
        try:
            previous[sig] = signal.signal(sig, on_signal)
        except ValueError:  # not the main thread: signals cannot be handled here
            break

    def interrupted() -> int | None:
        if not received:
            return None
        return 130 if received[0] == signal.SIGINT else 128 + received[0]

    def spawn(argv: Sequence[str], child_env: Mapping[str, str]) -> "subprocess.Popen[bytes]":
        return subprocess.Popen(
            list(argv), env=dict(child_env), stdin=subprocess.PIPE, start_new_session=True
        )

    procs: list[subprocess.Popen[bytes]] = []
    try:
        bridge_env = {k: v for k, v in env.items() if k != "ANTHROPIC_API_KEY"}
        bridge = spawn(bridge_argv, bridge_env)
        procs.append(bridge)

        deadline = time.monotonic() + health_timeout
        while True:
            if (status := interrupted()) is not None:
                return status
            if bridge.poll() is not None:
                print("bridge exited before it became healthy", file=sys.stderr)
                return 1
            if probe():
                break
            if time.monotonic() >= deadline:
                print(f"bridge did not become healthy within {health_timeout:g}s", file=sys.stderr)
                return 1
            time.sleep(POLL_INTERVAL)

        app = spawn(app_argv, env)
        procs.append(app)
        print(f"bridge pid {bridge.pid}, app pid {app.pid}", flush=True)

        while True:
            if (status := interrupted()) is not None:
                return status
            for proc in (bridge, app):
                code = proc.poll()
                if code is not None:
                    name = "bridge" if proc is bridge else "app"
                    print(f"{name} exited with status {code}; stopping", file=sys.stderr)
                    return _exit_status(code)
            time.sleep(WATCH_INTERVAL)
    finally:
        _stop_all(procs, stop_grace)
        for sig, handler in previous.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]


def main() -> int:
    port = pick_free_port()
    token = new_token()
    env = dict(os.environ)
    env[HOST_VAR] = HOST
    env[PORT_VAR] = str(port)
    env[TOKEN_VAR] = token
    return supervise(BRIDGE_ARGV, APP_ARGV, env, make_probe(HOST, port, token))


if __name__ == "__main__":
    sys.exit(main())
