"""Lifecycle tests use a stub server program injected as the start command (#100)."""

import http.client
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from agentbrowser import lifecycle
from mealplanner.demo import DEMO_OWNER

STUB_OK = """
import http.server, sys
port = int(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""
STUB_SILENT = (
    "import os, time\nprint('silent stub booting pid=%d' % os.getpid(), flush=True)\n"
    "time.sleep(60)\n"
)
STUB_EXIT = "import sys\nprint('stub boom', flush=True)\nsys.exit(3)\n"
# Exits at once but leaves a child in its process group.
STUB_EXIT_WITH_CHILD = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print("child pid=%d" % child.pid, flush=True)
sys.exit(3)
"""


def _factory(tmp_path: Path, body: str) -> Callable[[int, Path, str], list[str]]:
    script = tmp_path / "stub.py"
    script.write_text(body)

    def make(port: int, data_dir: Path, scenario: str) -> list[str]:
        return [sys.executable, "-u", str(script), str(port), "--data-dir", str(data_dir)]

    return make


def _tmp_dirs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob(lifecycle.DIR_PREFIX + "*"))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture(autouse=True)
def cleanup(state_dir: Path) -> object:
    yield
    for f in list(state_dir.glob("*.json")) if state_dir.exists() else []:
        lifecycle.stop(f.stem, state_dir=state_dir, grace=1)


def test_start_writes_state_and_stop_cleans_up(tmp_path: Path, state_dir: Path) -> None:
    handle = lifecycle.start(
        "a", "profile", command=_factory(tmp_path, STUB_OK), state_dir=state_dir, timeout=10
    )
    state = json.loads((state_dir / "a.json").read_text())
    assert state["url"] == f"http://127.0.0.1:{state['port']}"
    assert set(state) == {"url", "port", "pid", "data_dir", "log"}
    assert handle.url == state["url"]
    assert Path(state["log"]).parent == Path(state["data_dir"])
    assert os.getpgid(state["pid"]) == state["pid"]
    lifecycle.stop("a", state_dir=state_dir, grace=2)
    assert not _alive(state["pid"])
    assert not Path(state["data_dir"]).exists()
    assert not (state_dir / "a.json").exists()
    lifecycle.stop("a", state_dir=state_dir)  # second stop is clean


def test_never_answering_stub_fails_with_log_tail_and_leaves_nothing(
    tmp_path: Path, state_dir: Path
) -> None:
    before = _tmp_dirs()
    started = time.monotonic()
    with pytest.raises(lifecycle.LifecycleError, match="silent stub booting") as info:
        lifecycle.start(
            "s", "profile", command=_factory(tmp_path, STUB_SILENT), state_dir=state_dir, timeout=1
        )
    assert time.monotonic() - started < 10
    assert not (state_dir / "s.json").exists()
    assert _tmp_dirs() <= before
    found = re.search(r"pid=(\d+)", str(info.value))
    assert found
    assert not _alive(int(found.group(1)))


def test_exiting_stub_fails_saying_it_exited(tmp_path: Path, state_dir: Path) -> None:
    before = _tmp_dirs()
    with pytest.raises(lifecycle.LifecycleError, match="exited"):
        lifecycle.start(
            "x", "profile", command=_factory(tmp_path, STUB_EXIT), state_dir=state_dir, timeout=5
        )
    assert not (state_dir / "x.json").exists()
    assert _tmp_dirs() <= before


def test_early_exit_kills_children_left_in_the_group(tmp_path: Path, state_dir: Path) -> None:
    with pytest.raises(lifecycle.LifecycleError, match="exited") as info:
        lifecycle.start(
            "k",
            "profile",
            command=_factory(tmp_path, STUB_EXIT_WITH_CHILD),
            state_dir=state_dir,
            timeout=5,
        )
    found = re.search(r"child pid=(\d+)", str(info.value))
    assert found
    child_pid = int(found.group(1))
    deadline = time.monotonic() + 3
    while _alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child_pid)


def test_exiting_stub_error_has_log_tail(tmp_path: Path, state_dir: Path) -> None:
    with pytest.raises(lifecycle.LifecycleError, match="stub boom"):
        lifecycle.start(
            "x", "profile", command=_factory(tmp_path, STUB_EXIT), state_dir=state_dir, timeout=5
        )


def test_two_names_side_by_side_and_same_name_twice_fails(tmp_path: Path, state_dir: Path) -> None:
    make = _factory(tmp_path, STUB_OK)
    one = lifecycle.start("one", "profile", command=make, state_dir=state_dir, timeout=10)
    two = lifecycle.start("two", "profile", command=make, state_dir=state_dir, timeout=10)
    assert one.port != two.port
    assert one.data_dir != two.data_dir
    with pytest.raises(lifecycle.LifecycleError, match="make app-down APP_NAME=one"):
        lifecycle.start("one", "profile", command=make, state_dir=state_dir, timeout=10)
    assert _alive(one.pid)  # the failed second start did not disturb the first
    lifecycle.stop("one", state_dir=state_dir, grace=2)
    lifecycle.stop("two", state_dir=state_dir, grace=2)
    for handle in (one, two):
        assert not _alive(handle.pid)
        assert not handle.data_dir.exists()


def test_stale_state_file_is_cleaned(tmp_path: Path, state_dir: Path) -> None:
    state_dir.mkdir()
    data_dir = tmp_path / f"{lifecycle.DIR_PREFIX}stale"
    data_dir.mkdir()
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    (state_dir / "old.json").write_text(
        json.dumps(
            {
                "url": "http://127.0.0.1:1",
                "port": 1,
                "pid": done.pid,
                "data_dir": str(data_dir),
                "log": str(data_dir / "app.log"),
            }
        )
    )
    lifecycle.stop("old", state_dir=state_dir)
    assert not (state_dir / "old.json").exists()
    assert not data_dir.exists()


def test_stop_never_signals_a_process_it_did_not_start(tmp_path: Path, state_dir: Path) -> None:
    state_dir.mkdir()
    data_dir = tmp_path / f"{lifecycle.DIR_PREFIX}other"
    data_dir.mkdir()
    # A live group leader that is not an app started by `start`.
    bystander = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        (state_dir / "b.json").write_text(
            json.dumps(
                {
                    "url": "x",
                    "port": 1,
                    "pid": bystander.pid,
                    "data_dir": str(data_dir),
                    "log": str(data_dir / "app.log"),
                }
            )
        )
        lifecycle.stop("b", state_dir=state_dir, grace=1)
        assert bystander.poll() is None
        assert not (state_dir / "b.json").exists()
    finally:
        os.killpg(bystander.pid, signal.SIGKILL)
        bystander.wait()


def test_stop_does_not_signal_a_non_leader(tmp_path: Path, state_dir: Path) -> None:
    state_dir.mkdir()
    data_dir = tmp_path / f"{lifecycle.DIR_PREFIX}member"
    data_dir.mkdir()
    # Same session and group as the test runner, command line names the data dir, not a leader.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(data_dir)])
    try:
        (state_dir / "m.json").write_text(
            json.dumps({"pid": child.pid, "data_dir": str(data_dir), "port": 1})
        )
        lifecycle.stop("m", state_dir=state_dir, grace=1)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_state_file_cannot_make_stop_remove_an_arbitrary_directory(
    tmp_path: Path, state_dir: Path
) -> None:
    state_dir.mkdir()
    victim = tmp_path / "precious"
    victim.mkdir()
    (state_dir / "v.json").write_text(json.dumps({"pid": 999999, "data_dir": str(victim)}))
    lifecycle.stop("v", state_dir=state_dir)
    assert victim.exists()


def test_corrupt_state_file_is_removed(state_dir: Path) -> None:
    state_dir.mkdir()
    (state_dir / "c.json").write_text("not json")
    lifecycle.stop("c", state_dir=state_dir)
    assert not (state_dir / "c.json").exists()


def test_port_lost_to_another_process_is_retried(
    tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    taken = holder.getsockname()[1]
    real = lifecycle._free_port
    ports = iter([taken])
    monkeypatch.setattr(lifecycle, "_free_port", lambda: next(ports, None) or real())
    try:
        handle = lifecycle.start(
            "r", "profile", command=_factory(tmp_path, STUB_OK), state_dir=state_dir, timeout=10
        )
        assert handle.port != taken
    finally:
        holder.close()


def test_invalid_name_rejected(state_dir: Path) -> None:
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.start("../x", "profile", state_dir=state_dir)
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.stop("a/b", state_dir=state_dir)


def test_unknown_mode_rejected(state_dir: Path) -> None:
    with pytest.raises(lifecycle.LifecycleError, match="mode"):
        lifecycle.start("m", "profile", "wired", state_dir=state_dir)


def test_real_demo_app_serves_healthz(state_dir: Path) -> None:
    handle = lifecycle.start("t1", "profile", state_dir=state_dir)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", handle.port, timeout=5)
        conn.request("GET", "/healthz", headers={"Tailscale-User-Login": DEMO_OWNER})
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        lifecycle.stop("t1", state_dir=state_dir)
    assert not handle.data_dir.exists()
    assert not _alive(handle.pid)
