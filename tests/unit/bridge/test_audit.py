"""Audit log (#118, AC-11): one content-free line per run, values never logged."""

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridge.app import create_bridge_app
from bridge.config import BridgeSettings
from bridge.registry import Registry

TOKEN = "TOKEN-SENTINEL-" + "k" * 30
PORT = 8765
AUTH = {"Authorization": f"Bearer {TOKEN}"}
IN_S = "INPUT-SENTINEL-77"
OUT_S = "STDOUT-SENTINEL-88"
ERR_S = "STDERR-SENTINEL-99"
KEYS = {
    "event",
    "command",
    "caller",
    "input_bytes",
    "input_sha256",
    "outcome",
    "exit_status",
    "duration_ms",
}


def make_client(reg: Registry) -> TestClient:
    settings = BridgeSettings(host="127.0.0.1", port=PORT, token=TOKEN, parent_env={})
    return TestClient(create_bridge_app(settings, reg), base_url=f"http://127.0.0.1:{PORT}")


def post(client: TestClient, text: str = "hi", **kw: Any) -> Any:
    return client.post("/v1/run", json={"command": "stub", "input": text}, headers=AUTH, **kw)


def audit_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "bridge.audit"]


def test_audit_fields(
    stub_registry: Callable[..., Registry], caplog: pytest.LogCaptureFixture
) -> None:
    reg = stub_registry("import sys\nsys.stdout.write(sys.stdin.read())\n")
    text = "héllo wörld"
    with caplog.at_level(logging.INFO, logger="bridge.audit"):
        assert post(make_client(reg), text).status_code == 200
    (rec,) = audit_records(caplog)
    assert set(rec) == KEYS
    assert rec["event"] == "run"
    assert rec["command"] == "stub"
    assert rec["caller"] == "app"
    assert rec["input_bytes"] == len(text.encode())
    assert rec["input_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert rec["outcome"] == "exit"
    assert rec["exit_status"] == 0
    assert isinstance(rec["duration_ms"], int)


def test_outcomes(stub_registry: Callable[..., Registry], caplog: pytest.LogCaptureFixture) -> None:
    reg = stub_registry("import sys\nsys.exit(3)\n", max_input_bytes=5)
    client = make_client(reg)
    with caplog.at_level(logging.INFO, logger="bridge.audit"):
        post(client)
        post(client, "x" * 6)
        client.post("/v1/run", json={"command": "nope", "input": "x"}, headers=AUTH)
    recs = audit_records(caplog)
    assert [(r["outcome"], r["exit_status"]) for r in recs] == [
        ("exit", 3),
        ("input_too_large", None),
        ("unknown_command", None),
    ]


def test_audit_has_no_content(
    stub_registry: Callable[..., Registry], caplog: pytest.LogCaptureFixture
) -> None:
    body = (
        "import sys, time\n"
        f"sys.stderr.write({ERR_S!r})\nsys.stderr.flush()\n"
        "mode = sys.stdin.read().split('|')[1]\n"
        "if mode == 'sleep':\n    time.sleep(60)\n"
        "if mode == 'big':\n"
        f"    sys.stdout.write({OUT_S!r} * 500)\n"
        "else:\n"
        f"    sys.stdout.write({OUT_S!r})\n"
    )
    reg = stub_registry(body, timeout_seconds=1, max_output_bytes=1024, max_input_bytes=200)
    client = make_client(reg)
    with caplog.at_level(logging.DEBUG):
        for mode in ("ok", "sleep", "big"):
            post(client, f"{IN_S}|{mode}|{IN_S}")
        client.post(
            "/v1/run",
            json={"command": "stub", "input": IN_S},
            headers={"Authorization": f"Bearer {TOKEN}x"},
        )
    assert "bridge.audit" in {r.name for r in caplog.records}
    for secret in (IN_S, OUT_S, ERR_S, TOKEN):
        assert secret not in caplog.text


def test_rejected_requests_logged_without_values(
    stub_registry: Callable[..., Registry], caplog: pytest.LogCaptureFixture
) -> None:
    reg = stub_registry("pass\n")
    client = make_client(reg)
    bad_host = "evil-host-sentinel.example"
    wrong = "WRONG-TOKEN-SENTINEL"
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/v1/run",
            json={"command": "stub", "input": "x"},
            headers={"Authorization": f"Bearer {wrong}"},
        )
        client.post(
            "/v1/run", json={"command": "stub", "input": "x"}, headers={**AUTH, "Host": bad_host}
        )
    recs = audit_records(caplog)
    assert recs == [
        {"event": "rejected", "reason": "unauthorized"},
        {"event": "rejected", "reason": "forbidden_host"},
    ]
    assert wrong not in caplog.text
    assert bad_host not in caplog.text


def test_entry_point_logging_emits_audit_line(
    stub_registry: Callable[..., Registry], capsys: pytest.CaptureFixture[str]
) -> None:
    """Shipped setup (no caplog.at_level): the audit INFO line reaches stderr."""
    from bridge.audit import configure_audit_logging

    audit = logging.getLogger("bridge.audit")
    saved = (audit.level, audit.handlers[:], audit.propagate)
    try:
        configure_audit_logging()
        configure_audit_logging()  # idempotent
        assert audit.isEnabledFor(logging.INFO)
        res = post(make_client(stub_registry("print('x')\n")))
        assert res.status_code == 200
        lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("{")]
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "run"
    finally:
        audit.setLevel(saved[0])
        audit.handlers[:] = saved[1]
        audit.propagate = saved[2]
