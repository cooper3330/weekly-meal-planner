"""Containment (#118, AC-6): process-group timeout, output cap, 429 concurrency limit."""

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from bridge.app import create_bridge_app
from bridge.config import BridgeSettings
from bridge.registry import Registry

TOKEN = "k" * 40
PORT = 8765
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CAP = 1024


def make_client(reg: Registry, **kw: Any) -> TestClient:
    settings = BridgeSettings(host="127.0.0.1", port=PORT, token=TOKEN, parent_env={})
    return TestClient(
        create_bridge_app(settings, reg, **kw),
        base_url=f"http://127.0.0.1:{PORT}",
        raise_server_exceptions=False,
    )


def post(client: TestClient, text: str = "hi") -> Any:
    return client.post("/v1/run", json={"command": "stub", "input": text}, headers=AUTH)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_gone(pid: int, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.02)
    return not alive(pid)


def read_pids(path: Path) -> list[int]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if path.exists() and len(path.read_text().split()) == 2:
            break
        time.sleep(0.02)
    return [int(p) for p in path.read_text().split()]


SPAWN_GRANDCHILD = (
    "import os, subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "open({pids!r}, 'w').write(f'{{os.getpid()}} {{g.pid}}')\n"
)


def test_timeout_kills_process_group(
    tmp_path: Path, stub_registry: Callable[..., Registry]
) -> None:
    pids = tmp_path / "pids"
    reg = stub_registry(
        SPAWN_GRANDCHILD.format(pids=str(pids)) + "time.sleep(60)\n", timeout_seconds=1
    )
    started = time.monotonic()
    res = post(make_client(reg))
    assert res.status_code == 504
    assert res.json() == {"error": "timeout"}
    assert time.monotonic() - started < 3
    for pid in read_pids(pids):
        assert wait_gone(pid)


def test_timeout_when_pipes_closed(tmp_path: Path, stub_registry: Callable[..., Registry]) -> None:
    pidfile = tmp_path / "pid"
    reg = stub_registry(
        "import os, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "os.close(1)\nos.close(2)\ntime.sleep(60)\n",
        timeout_seconds=1,
    )
    client = make_client(reg)
    result: dict[str, Any] = {}

    def run() -> None:
        result["res"] = post(client)

    started = time.monotonic()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), "request hung past the deadline"
    assert result["res"].status_code == 504
    assert result["res"].json() == {"error": "timeout"}
    assert time.monotonic() - started < 3
    assert wait_gone(int(pidfile.read_text()))
    assert post(client).status_code != 429


def test_grandchild_killed_after_normal_exit(
    tmp_path: Path, stub_registry: Callable[..., Registry]
) -> None:
    pids = tmp_path / "pids"
    reg = stub_registry(
        SPAWN_GRANDCHILD.format(pids=str(pids)) + "print('ok')\n", timeout_seconds=5
    )
    started = time.monotonic()
    res = post(make_client(reg))
    assert res.status_code == 200
    assert res.json()["stdout"].strip() == "ok"
    assert time.monotonic() - started < 3
    for pid in read_pids(pids):
        assert wait_gone(pid)


def test_output_over_cap_is_error(stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry(
        "import sys\nsys.stdout.write('a' * (int(sys.stdin.read()) ))\n",
        max_output_bytes=CAP,
    )
    client = make_client(reg)
    over = post(client, str(CAP + 1))
    assert over.status_code == 502
    assert over.json() == {"error": "output_too_large"}
    assert "stdout" not in over.text
    exact = post(client, str(CAP))
    assert exact.status_code == 200
    assert exact.json()["stdout"] == "a" * CAP


def test_endless_output_is_killed_quickly(stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry(
        "import sys\nwhile True:\n    sys.stdout.write('a' * 4096)\n    sys.stdout.flush()\n",
        max_output_bytes=CAP,
        timeout_seconds=10,
    )
    started = time.monotonic()
    res = post(make_client(reg))
    assert res.status_code == 502
    assert time.monotonic() - started < 3


def test_large_stderr_does_not_block(stub_registry: Callable[..., Registry]) -> None:
    reg = stub_registry(
        "import sys\nsys.stderr.write('e' * (1 << 20))\nsys.stderr.flush()\nprint('small')\n",
        timeout_seconds=5,
    )
    res = post(make_client(reg))
    assert res.status_code == 200
    assert res.json()["stdout"].strip() == "small"


def test_concurrency_limit_429(tmp_path: Path, stub_registry: Callable[..., Registry]) -> None:
    release = tmp_path / "release"
    started = tmp_path / "started"
    second = tmp_path / "second"
    reg = stub_registry(
        "import os, sys, time\n"
        f"if os.path.exists({str(started)!r}):\n"
        f"    open({str(second)!r}, 'w').write('x')\n"
        "    sys.exit(0)\n"
        f"open({str(started)!r}, 'w').write('x')\n"
        f"while not os.path.exists({str(release)!r}):\n"
        "    time.sleep(0.02)\n"
        "print('first')\n",
        max_concurrent=1,
        timeout_seconds=5,
    )
    client = make_client(reg)
    first: list[Any] = []
    t = threading.Thread(target=lambda: first.append(post(client)))
    t.start()
    deadline = time.monotonic() + 3
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    busy = post(client)
    assert busy.status_code == 429
    assert busy.json() == {"error": "busy"}
    assert busy.headers["Retry-After"] == "1"
    assert not second.exists()
    release.write_text("x")
    t.join(5)
    assert first[0].status_code == 200
    assert post(client).status_code == 200


def test_slot_released_on_every_path(
    tmp_path: Path, stub_registry: Callable[..., Registry], registry_for: Callable[..., Registry]
) -> None:
    mode = tmp_path / "mode"
    reg = stub_registry(
        "import sys, time\n"
        f"mode = open({str(mode)!r}).read()\n"
        "if mode == 'sleep':\n    time.sleep(60)\n"
        "if mode == 'big':\n    sys.stdout.write('a' * 5000)\n"
        "print('ok')\n",
        max_concurrent=1,
        timeout_seconds=1,
        max_output_bytes=CAP,
    )
    client = make_client(reg)
    for m, status in (("sleep", 504), ("big", 502), ("ok", 200)):
        mode.write_text(m)
        assert post(client).status_code == status
    assert post(client).status_code == 200

    missing = make_client(registry_for(tmp_path / "nope", max_concurrent=1))
    assert post(missing).status_code == 503
    assert post(missing).status_code == 503

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("boom")

    raising = make_client(reg, runner=boom)
    assert post(raising).status_code == 500
    assert post(raising).status_code == 500  # 500 again, never 429
