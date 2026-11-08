"""The `make run` supervisor (#120, #112 AC-1).

The offline guard blocks loopback connects in the test process, so the probe is injected here
and the end-to-end runs use a subprocess. Stub children are Python scripts in `tmp_path`.
"""

import http.client
import json
import os
import re
import select
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from bridge import supervisor
from bridge.supervisor import supervise

ROOT = Path(__file__).resolve().parents[3]
SRC = str(ROOT / "src")

# argv: <name> <mode>. Records its pid and a start event under $STUB_DIR, then behaves per mode.
STUB = """
import json, os, signal, subprocess, sys, time

d = os.environ["STUB_DIR"]
name = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else "sleep"
with open(f"{d}/{name}.pid", "w") as f:
    f.write(str(os.getpid()))
with open(f"{d}/events", "a") as f:
    f.write(name + "\\n")
with open(f"{d}/{name}.env", "w") as f:
    json.dump(dict(os.environ), f)
if mode == "exit3":
    sys.exit(3)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "grandchild":
    gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(f"{d}/{name}.grandchild", "w") as f:
        f.write(str(gc.pid))
deadline = time.time() + 60
while time.time() < deadline:
    time.sleep(0.05)
"""


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    path = tmp_path / "stub.py"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(STUB))
    path.chmod(0o755)
    return path


@pytest.fixture
def stub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("STUB_DIR", str(tmp_path))
    return dict(os.environ)


@pytest.fixture(autouse=True)
def reap_stubs(tmp_path: Path) -> Iterator[None]:
    """Kill anything a failing test left behind."""
    yield
    for pid_file in tmp_path.glob("*.pid"):
        kill_quietly(pid_file)
    for pid_file in tmp_path.glob("*.grandchild"):
        kill_quietly(pid_file)


def kill_quietly(pid_file: Path) -> None:
    try:
        os.kill(int(pid_file.read_text()), signal.SIGKILL)
    except (ProcessLookupError, ValueError, OSError):
        pass


def pid_of(tmp_path: Path, name: str, suffix: str = "pid") -> int:
    return int((tmp_path / f"{name}.{suffix}").read_text())


def wait_for(predicate, timeout: float = 8.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def pid_gone(pid: int) -> bool:
    """True when the process no longer exists (a zombie counts as gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return state == "" or state.startswith("Z")


def events(tmp_path: Path) -> list[str]:
    path = tmp_path / "events"
    return path.read_text().split() if path.exists() else []


def run_in_thread(fn) -> threading.Thread:  # type: ignore[no-untyped-def]
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread


def test_bridge_healthy_before_app_starts(
    stub: Path, stub_env: dict[str, str], tmp_path: Path
) -> None:
    calls = {"n": 0}

    def probe() -> bool:
        if calls["n"] == 0:  # let the bridge stub record that it started
            assert wait_for(lambda: events(tmp_path) == ["bridge"])
        calls["n"] += 1
        with open(tmp_path / "events", "a") as f:
            f.write(f"probe{calls['n']}\n")
        return calls["n"] >= 3

    def stop_when_both_up() -> None:
        wait_for(lambda: (tmp_path / "app.pid").exists())
        os.kill(pid_of(tmp_path, "app"), signal.SIGKILL)

    run_in_thread(stop_when_both_up)
    rc = supervise([str(stub), "bridge"], [str(stub), "app"], stub_env, probe)
    assert rc != 0
    assert events(tmp_path) == ["bridge", "probe1", "probe2", "probe3", "app"]


def test_health_timeout_stops_bridge(stub: Path, stub_env: dict[str, str], tmp_path: Path) -> None:
    started = time.monotonic()
    rc = supervise(
        [str(stub), "bridge"],
        [str(stub), "app"],
        stub_env,
        lambda: False,
        health_timeout=0.6,
        stop_grace=2.0,
    )
    assert rc == 1
    assert time.monotonic() - started < 0.6 + 2.0 + 1.0
    assert pid_gone(pid_of(tmp_path, "bridge"))
    assert not (tmp_path / "app.pid").exists()
    assert events(tmp_path) == ["bridge"]


def test_bridge_exits_during_startup(stub: Path, stub_env: dict[str, str], tmp_path: Path) -> None:
    rc = supervise(
        [str(stub), "bridge", "exit3"],
        [str(stub), "app"],
        stub_env,
        lambda: False,
        health_timeout=5.0,
    )
    assert rc == 1
    assert not (tmp_path / "app.pid").exists()


@pytest.mark.parametrize("victim", ["bridge", "app"])
def test_killing_either_child_stops_both(
    stub: Path, stub_env: dict[str, str], tmp_path: Path, victim: str
) -> None:
    other = "app" if victim == "bridge" else "bridge"

    def kill_victim() -> None:
        assert wait_for(
            lambda: (tmp_path / "app.pid").exists() and (tmp_path / "bridge.pid").exists()
        )
        time.sleep(0.1)
        os.kill(pid_of(tmp_path, victim), signal.SIGKILL)

    run_in_thread(kill_victim)
    rc = supervise([str(stub), "bridge"], [str(stub), "app"], stub_env, lambda: True)
    assert rc != 0  # an unexpected exit is a failure
    assert wait_for(lambda: pid_gone(pid_of(tmp_path, other)), timeout=2.0)
    assert pid_gone(pid_of(tmp_path, victim))


def test_clean_child_exit_is_still_a_failure(
    stub: Path, stub_env: dict[str, str], tmp_path: Path
) -> None:
    rc = supervise(
        [str(stub), "bridge"],
        [sys.executable, "-c", "pass"],
        stub_env,
        lambda: True,
    )
    assert rc != 0
    assert pid_gone(pid_of(tmp_path, "bridge"))


def test_child_ignoring_sigterm_is_killed(
    stub: Path, stub_env: dict[str, str], tmp_path: Path
) -> None:
    def kill_app() -> None:
        assert wait_for(lambda: (tmp_path / "app.pid").exists())
        time.sleep(0.2)  # let the bridge stub finish installing its handler
        os.kill(pid_of(tmp_path, "app"), signal.SIGKILL)

    run_in_thread(kill_app)
    started = time.monotonic()
    supervise(
        [str(stub), "bridge", "ignore_term"],
        [str(stub), "app"],
        stub_env,
        lambda: True,
        stop_grace=1.0,
    )
    assert time.monotonic() - started < 1.0 + 1.0 + 1.0
    assert pid_gone(pid_of(tmp_path, "bridge"))


def test_grandchildren_stopped(stub: Path, stub_env: dict[str, str], tmp_path: Path) -> None:
    def kill_app() -> None:
        assert wait_for(lambda: (tmp_path / "bridge.grandchild").exists())
        assert wait_for(lambda: (tmp_path / "app.pid").exists())
        os.kill(pid_of(tmp_path, "app"), signal.SIGKILL)

    run_in_thread(kill_app)
    supervise([str(stub), "bridge", "grandchild"], [str(stub), "app"], stub_env, lambda: True)
    grandchild = pid_of(tmp_path, "bridge", "grandchild")
    assert wait_for(lambda: pid_gone(grandchild), timeout=3.0)


def test_exception_in_probe_still_tears_down(
    stub: Path, stub_env: dict[str, str], tmp_path: Path
) -> None:
    def probe() -> bool:
        assert wait_for(lambda: (tmp_path / "bridge.pid").exists())
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        supervise([str(stub), "bridge"], [str(stub), "app"], stub_env, probe)
    assert pid_gone(pid_of(tmp_path, "bridge"))


def run_supervise_script(tmp_path: Path, stub: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    """Run the real `main()` with stub children and an always-healthy probe, in a subprocess."""
    script = tmp_path / "run_supervise.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            from bridge import supervisor

            supervisor.BRIDGE_ARGV = [{str(stub)!r}, "bridge"]
            supervisor.APP_ARGV = [{str(stub)!r}, "app"]
            supervisor.make_probe = lambda host, port, token: (lambda: True)
            sys.exit(supervisor.main())
            """
        )
    )
    full_env = {**env, "PYTHONPATH": SRC}
    return subprocess.Popen(
        [sys.executable, str(script)],
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_sigterm_to_supervisor_stops_both(
    stub: Path, stub_env: dict[str, str], tmp_path: Path
) -> None:
    check_signal_stops_both(stub, stub_env, tmp_path, signal.SIGTERM, 143)


def test_sigint_returns_130(stub: Path, stub_env: dict[str, str], tmp_path: Path) -> None:
    check_signal_stops_both(stub, stub_env, tmp_path, signal.SIGINT, 130)


def test_sighup_to_supervisor_stops_both(
    stub: Path, stub_env: dict[str, str], tmp_path: Path
) -> None:
    check_signal_stops_both(stub, stub_env, tmp_path, signal.SIGHUP, 129)


def check_signal_stops_both(
    stub: Path, stub_env: dict[str, str], tmp_path: Path, sig: signal.Signals, expected: int
) -> None:
    proc = run_supervise_script(tmp_path, stub, stub_env)
    try:
        assert wait_for(
            lambda: (tmp_path / "app.pid").exists() and (tmp_path / "bridge.pid").exists()
        )
        proc.send_signal(sig)
        proc.communicate(timeout=10)
        assert proc.returncode == expected
        assert pid_gone(pid_of(tmp_path, "bridge"))
        assert pid_gone(pid_of(tmp_path, "app"))
    finally:
        proc.kill()
        proc.communicate()


def test_secret_not_exposed(stub: Path, stub_env: dict[str, str], tmp_path: Path) -> None:
    # main() generates the port and token itself; the stubs reveal them through their environment.
    env = {**stub_env, "ANTHROPIC_API_KEY": "sk-ant-test-key"}
    for var in ("MEALPLANNER_BRIDGE_TOKEN", "MEALPLANNER_BRIDGE_PORT", "MEALPLANNER_BRIDGE_HOST"):
        env.pop(var, None)
    proc = run_supervise_script(tmp_path, stub, env)
    try:
        assert wait_for(
            lambda: (tmp_path / "app.env").exists() and (tmp_path / "bridge.env").exists()
        )
        bridge_env = json.loads((tmp_path / "bridge.env").read_text())
        app_env = json.loads((tmp_path / "app.env").read_text())
        token = bridge_env["MEALPLANNER_BRIDGE_TOKEN"]
        port = bridge_env["MEALPLANNER_BRIDGE_PORT"]
        assert len(token) >= 32
        for child_env in (bridge_env, app_env):
            assert child_env["MEALPLANNER_BRIDGE_HOST"] == "127.0.0.1"
            assert child_env["MEALPLANNER_BRIDGE_TOKEN"] == token
            assert child_env["MEALPLANNER_BRIDGE_PORT"] == port
        for name in ("bridge", "app"):
            args = subprocess.run(
                ["ps", "-o", "args=", "-p", str(pid_of(tmp_path, name))],
                capture_output=True,
                text=True,
            ).stdout
            assert args.strip()
            assert token not in args
        assert "ANTHROPIC_API_KEY" not in bridge_env
        assert app_env["ANTHROPIC_API_KEY"] == "sk-ant-test-key"
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=10)
        assert token not in out + err
        assert port not in out + err
        assert re.search(r"\d+", out)  # the pids line is there
    finally:
        proc.kill()
        proc.communicate()


def test_pids_line_printed_once_both_are_up(
    stub: Path, stub_env: dict[str, str], tmp_path: Path
) -> None:
    proc = run_supervise_script(tmp_path, stub, stub_env)
    try:
        assert wait_for(lambda: (tmp_path / "app.pid").exists())
        assert proc.stdout is not None
        deadline = time.monotonic() + 8
        first = ""
        while not first and time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.1)[0]:
                first = proc.stdout.readline()
        assert first, "no pids line"
        proc.send_signal(signal.SIGTERM)
        rest, _ = proc.communicate(timeout=10)
        assert rest.strip() == ""  # printed once, nothing after it
        lines = first.strip().splitlines()
        assert len(lines) == 1
        assert str(pid_of(tmp_path, "bridge")) in lines[0]
        assert str(pid_of(tmp_path, "app")) in lines[0]
    finally:
        proc.kill()
        proc.communicate()


def test_pick_port_and_token_are_fresh() -> None:
    assert 1 <= supervisor.pick_free_port() <= 65535
    assert supervisor.new_token() != supervisor.new_token()
    assert len(supervisor.new_token()) >= 32


def test_probe_sends_token_to_healthz(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b"{}"

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            seen["addr"] = (host, port)

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            seen["req"] = (method, path, headers)

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", FakeConnection)
    probe = supervisor.make_probe("127.0.0.1", 9999, "tok" * 12)
    assert probe() is True
    assert seen["addr"] == ("127.0.0.1", 9999)
    assert seen["req"] == ("GET", "/healthz", {"Authorization": "Bearer " + "tok" * 12})


def test_probe_false_when_connection_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class Refusing:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

        def request(self, *args: object, **kwargs: object) -> None:
            raise ConnectionRefusedError

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", Refusing)
    assert supervisor.make_probe("127.0.0.1", 9999, "t" * 40)() is False


def test_make_run_uses_supervisor() -> None:
    makefile = (ROOT / "Makefile").read_text()
    match = re.search(r"^run:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert match is not None
    assert "-m bridge.supervisor" in match.group(1)


# --- real processes: the real bridge and the real app on loopback ports -------------------------


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_real(
    tmp_path: Path,
    seen: list[str] | None = None,
    pin: tuple[int, str] | None = None,
) -> tuple[subprocess.Popen[str], int, int]:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(
        PYTHONPATH=SRC,
        MEALPLANNER_DATA_DIR=str(tmp_path / "data"),
        MEALPLANNER_PORT=str(free_port()),
        MEALPLANNER_DEV_LOGIN="dev@example.com",
    )
    cmd = [sys.executable, "-m", "bridge.supervisor"]
    if pin is not None:
        # Same real BRIDGE_ARGV/APP_ARGV, but a known port and token, so the test can look for them.
        script = tmp_path / "run_real.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import sys
                from bridge import supervisor

                supervisor.pick_free_port = lambda: {pin[0]}
                supervisor.new_token = lambda: {pin[1]!r}
                sys.exit(supervisor.main())
                """
            )
        )
        cmd = [sys.executable, str(script)]
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 1)
        if not ready:
            continue
        line = proc.stdout.readline()  # the bridge's access log shares this stream
        if seen is not None:
            seen.append(line)
        found = re.fullmatch(r"bridge pid (\d+), app pid (\d+)\s*", line)
        if found:
            return proc, int(found[1]), int(found[2])
        assert line, "the supervisor exited before printing its pids line"
    raise AssertionError("the supervisor printed no pids line")


def test_supervisor_sigkill_leaves_no_orphan(tmp_path: Path) -> None:
    proc, bridge_pid, app_pid = start_real(tmp_path)
    try:
        proc.kill()  # SIGKILL: no teardown code runs, only the lifeline can stop the children
        proc.wait(timeout=5)
        assert wait_for(lambda: pid_gone(bridge_pid) and pid_gone(app_pid), timeout=10.0)
    finally:
        for pid in (bridge_pid, app_pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.communicate()


def test_real_run_never_prints_token_or_bridge_port(tmp_path: Path) -> None:
    seen: list[str] = []
    port = str(free_port())
    token = "pinned-token-for-leak-check-0123456789"
    proc, bridge_pid, app_pid = start_real(tmp_path, seen, pin=(int(port), token))
    try:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=15)
        everything = "".join(seen) + out + err
        assert "bridge pid" in everything
        assert token not in everything
        assert not re.search(rf"(?<!\d){port}(?!\d)", everything)
    finally:
        for pid in (bridge_pid, app_pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
        proc.communicate()


def test_real_run_without_login_reports_config_error_and_stops(tmp_path: Path) -> None:
    # Owner check found this: the real app refuses to start with no login configured (ADR 0012).
    # The supervisor must then stop the bridge and exit with the app's status, reason shown.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "MEALPLANNER_OWNER_LOGIN", "MEALPLANNER_DEV_LOGIN")
    }
    env.update(
        PYTHONPATH=SRC,
        MEALPLANNER_DATA_DIR=str(tmp_path / "data"),
        MEALPLANNER_PORT=str(free_port()),
    )
    proc = subprocess.run(
        [sys.executable, "-m", "bridge.supervisor"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 3
    assert "ConfigError" in proc.stderr
    assert "MEALPLANNER_OWNER_LOGIN" in proc.stderr
    assert "app exited with status 3; stopping" in proc.stderr


def test_real_run_kill_bridge_stops_app(tmp_path: Path) -> None:
    proc, bridge_pid, app_pid = start_real(tmp_path)
    try:
        # start_real returns only once the pids line is out, i.e. both children are up.
        os.kill(bridge_pid, signal.SIGKILL)
        proc.wait(timeout=15)
        assert proc.returncode != 0
        assert wait_for(lambda: pid_gone(app_pid), timeout=10.0)
    finally:
        for pid in (bridge_pid, app_pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
        proc.communicate()
