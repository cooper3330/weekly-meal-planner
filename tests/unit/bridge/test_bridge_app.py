"""Bridge HTTP app (#117, AC-3/4/5/7): access checks, validation, hostile input."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridge.app import create_bridge_app
from bridge.config import BridgeSettings
from bridge.registry import Registry

TOKEN = "k" * 40
PORT = 8765
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SENTINEL = "SENTINEL-INPUT-9f3a"

RAN_BODY = "import sys\nopen({marker!r}, 'w').write('ran')\nsys.stdout.write(sys.stdin.read())\n"


def settings(parent_env: dict[str, str] | None = None, host: str = "127.0.0.1") -> BridgeSettings:
    return BridgeSettings(host=host, port=PORT, token=TOKEN, parent_env=parent_env or {})


@pytest.fixture
def marker(tmp_path: Path) -> Path:
    return tmp_path / "ran"


@pytest.fixture
def client(marker: Path, stub_registry: Callable[..., Registry]) -> TestClient:
    reg = stub_registry(RAN_BODY.format(marker=str(marker)), max_input_bytes=100)
    return TestClient(create_bridge_app(settings(), reg), base_url=f"http://127.0.0.1:{PORT}")


def run(client: TestClient, **kw: Any) -> Any:
    body = kw.pop("json", {"command": "stub", "input": "hi"})
    headers = {**AUTH, **kw.pop("headers", {})}
    return client.post("/v1/run", json=body, headers=headers, **kw)


def test_happy_path(client: TestClient, marker: Path) -> None:
    r = run(client)
    assert r.status_code == 200
    body = r.json()
    assert body["exit_code"] == 0
    assert body["stdout"] == "hi"
    assert isinstance(body["duration_ms"], int)
    assert marker.exists()


def test_no_docs_or_openapi(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers=AUTH).status_code == 404


def test_healthz_requires_token(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 401
    r = client.get("/healthz", headers=AUTH)
    assert (r.status_code, r.json()) == (200, {"status": "ok"})


@pytest.mark.parametrize(
    ("headers", "status", "kind"),
    [
        ({"Authorization": None}, 401, "unauthorized"),
        ({"Authorization": "Bearer wrong"}, 401, "unauthorized"),
        ({"Authorization": f"Basic {TOKEN}"}, 401, "unauthorized"),
        ({"Authorization": f"Bearer {TOKEN} "}, 401, "unauthorized"),
        ({"Authorization": f"bearer {TOKEN}x"}, 401, "unauthorized"),
        ({"Host": "evil.example:8765"}, 403, "forbidden_host"),
        ({"Host": "127.0.0.1:9999"}, 403, "forbidden_host"),
        ({"Host": "127.0.0.1"}, 403, "forbidden_host"),
        ({"Host": "[::1]:8765"}, 403, "forbidden_host"),
        ({"Host": ""}, 403, "forbidden_host"),
        ({"Origin": f"http://localhost:{PORT}"}, 403, "forbidden_origin"),
        ({"Origin": "null"}, 403, "forbidden_origin"),
        ({"Content-Type": "text/plain"}, 415, "unsupported_media_type"),
        ({"Content-Type": "application/x-www-form-urlencoded"}, 415, "unsupported_media_type"),
    ],
)
def test_access_checks_run_nothing(
    client: TestClient,
    marker: Path,
    headers: dict[str, str | None],
    status: int,
    kind: str,
) -> None:
    hdrs = {**AUTH, **headers}
    hdrs = {k: v for k, v in hdrs.items() if v is not None}
    if "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/json"
    r = client.post("/v1/run", content='{"command": "stub", "input": "hi"}', headers=hdrs)
    assert (r.status_code, r.json()) == (status, {"error": kind})
    assert not marker.exists()


def test_localhost_host_accepted(client: TestClient) -> None:
    r = client.get("/healthz", headers={**AUTH, "Host": f"localhost:{PORT}"})
    assert r.status_code == 200


def test_ipv6_host_only_when_bound_to_it(
    marker: Path, stub_registry: Callable[..., Registry]
) -> None:
    reg = stub_registry(RAN_BODY.format(marker=str(marker)))
    c = TestClient(create_bridge_app(settings(host="::1"), reg), base_url=f"http://[::1]:{PORT}")
    assert c.get("/healthz", headers=AUTH).status_code == 200


def test_check_order_host_before_origin_before_token(client: TestClient) -> None:
    r = client.get("/healthz", headers={"Host": "evil.example", "Origin": "null"})
    assert r.json() == {"error": "forbidden_host"}
    r = client.get("/healthz", headers={"Origin": "null"})
    assert r.json() == {"error": "forbidden_origin"}


def test_preflight_gets_no_cors_headers(client: TestClient, marker: Path) -> None:
    r = client.options(
        "/v1/run",
        headers={
            "Origin": f"http://localhost:{PORT}",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert not [h for h in r.headers if h.lower().startswith("access-control-")]
    assert r.status_code >= 400
    assert not marker.exists()


def test_unknown_command_404(client: TestClient, marker: Path) -> None:
    r = run(client, json={"command": "bash", "input": "x"})
    assert (r.status_code, r.json()) == (404, {"error": "unknown_command"})
    assert not marker.exists()


@pytest.mark.parametrize(
    "body",
    [
        {"command": "stub", "input": SENTINEL, "extra": SENTINEL},
        {"command": "stub", "input": [SENTINEL]},
        {"command": "stub", "input": 5},
        {"command": "../claude", "input": SENTINEL},
        {"command": "stub"},
        {"input": SENTINEL},
    ],
)
def test_validation_error_does_not_echo_input(
    client: TestClient, marker: Path, body: dict[str, Any]
) -> None:
    r = run(client, json=body)
    assert (r.status_code, r.json()) == (422, {"error": "invalid_request"})
    assert SENTINEL not in r.text
    assert not marker.exists()


def test_malformed_json_422_without_echo(client: TestClient) -> None:
    r = client.post(
        "/v1/run",
        content=f'{{"command": "stub", "input": "{SENTINEL}',
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert (r.status_code, r.json()) == (422, {"error": "invalid_request"})
    assert SENTINEL not in r.text


def test_input_too_large_counts_utf8_bytes(client: TestClient, marker: Path) -> None:
    # 50 two-byte characters = 100 bytes: exactly at the cap.
    assert run(client, json={"command": "stub", "input": "é" * 50}).status_code == 200
    marker.unlink()
    # 51 characters is 102 bytes; a character count (51) would have passed.
    r = run(client, json={"command": "stub", "input": "é" * 51})
    assert (r.status_code, r.json()) == (413, {"error": "input_too_large"})
    assert not marker.exists()


def test_one_byte_over(client: TestClient) -> None:
    assert run(client, json={"command": "stub", "input": "a" * 100}).status_code == 200
    assert run(client, json={"command": "stub", "input": "a" * 101}).status_code == 413


def test_content_length_over_cap_413_before_body_read(client: TestClient, marker: Path) -> None:
    big = "a" * (100 + 16384 + 1)
    r = client.post(
        "/v1/run",
        content=big,
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert (r.status_code, r.json()) == (413, {"error": "input_too_large"})
    assert not marker.exists()


def test_lone_surrogate_is_invalid_request(client: TestClient) -> None:
    r = client.post(
        "/v1/run",
        content=b'{"command": "stub", "input": "\\ud800"}',
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert (r.status_code, r.json()) == (422, {"error": "invalid_request"})


def test_hostile_input_passed_unchanged(
    tmp_path: Path, stub_registry: Callable[..., Registry]
) -> None:
    stdin_f, argv_f, pwned = tmp_path / "stdin", tmp_path / "argv.json", tmp_path / "pwned"
    reg = stub_registry(
        "import json, sys\n"
        f"open({str(stdin_f)!r}, 'wb').write(sys.stdin.buffer.read())\n"
        f"json.dump(sys.argv[1:], open({str(argv_f)!r}, 'w'))\n",
        args=["-p", "--model", "sonnet"],
        max_input_bytes=10_000,
    )
    c = TestClient(create_bridge_app(settings(), reg), base_url=f"http://127.0.0.1:{PORT}")
    hostile = (
        f"; rm -rf ~ && echo pwned $(id) | cat; touch {pwned}\n"
        f"`whoami` > {pwned} *\n"
        "--dangerously-skip-permissions\n"
        "\nclaude -p 'second'\n"
        "naïve 日本語 🍜\n"
    )
    r = run(c, json={"command": "stub", "input": hostile})
    assert r.status_code == 200
    assert stdin_f.read_bytes() == hostile.encode("utf-8")
    assert json.loads(argv_f.read_text()) == ["-p", "--model", "sonnet"]
    assert not pwned.exists()


def test_child_env_minimal(tmp_path: Path, stub_registry: Callable[..., Registry]) -> None:
    out = tmp_path / "env.json"
    reg = stub_registry(
        # Python itself may add LC_CTYPE (PEP 538); that is the stub's interpreter, not the bridge.
        "import json, os\n"
        f"env = {{k: v for k, v in os.environ.items() if k != 'LC_CTYPE'}}\n"
        f"json.dump(env, open({str(out)!r}, 'w'))\n",
        env={"FIXED": "1", "OFF": "1"},
        passthrough=["HOME", "LANG"],
    )
    parent = {
        "HOME": str(tmp_path),
        "ANTHROPIC_API_KEY": "sk-x",
        "MEALPLANNER_BRIDGE_TOKEN": TOKEN,
        "SECRET_X": "x",
    }
    c = TestClient(create_bridge_app(settings(parent), reg), base_url=f"http://127.0.0.1:{PORT}")
    assert run(c).status_code == 200
    assert json.loads(out.read_text()) == {"FIXED": "1", "OFF": "1", "HOME": str(tmp_path)}


def test_nonzero_exit_returns_200_with_exit_code(
    stub_registry: Callable[..., Registry],
) -> None:
    reg = stub_registry("import sys\nprint('{\"is_error\": true}')\nsys.exit(3)\n")
    c = TestClient(create_bridge_app(settings(), reg), base_url=f"http://127.0.0.1:{PORT}")
    r = run(c)
    assert r.status_code == 200
    assert r.json()["exit_code"] == 3
    assert json.loads(r.json()["stdout"]) == {"is_error": True}


def test_program_not_found_503(tmp_path: Path, registry_for: Callable[..., Registry]) -> None:
    missing = registry_for(tmp_path / "nope")
    c = TestClient(create_bridge_app(settings(), missing), base_url=f"http://127.0.0.1:{PORT}")
    r = run(c)
    assert (r.status_code, r.json()) == (503, {"error": "program_not_found"})
    plain = tmp_path / "plain.txt"
    plain.write_text("x")
    plain.chmod(0o644)
    c = TestClient(
        create_bridge_app(settings(), registry_for(plain)), base_url=f"http://127.0.0.1:{PORT}"
    )
    r = run(c)
    assert (r.status_code, r.json()) == (503, {"error": "program_not_found"})


def test_spawn_failed_body_is_fixed(tmp_path: Path, registry_for: Callable[..., Registry]) -> None:
    prog = tmp_path / "bad"
    prog.write_text("#!/nonexistent/secret-interp\n")
    prog.chmod(0o755)
    c = TestClient(
        create_bridge_app(settings(), registry_for(prog)), base_url=f"http://127.0.0.1:{PORT}"
    )
    r = run(c)
    assert (r.status_code, r.json()) == (500, {"error": "spawn_failed"})
    assert "secret-interp" not in r.text


def test_stderr_never_returned_or_logged(
    stub_registry: Callable[..., Registry], caplog: pytest.LogCaptureFixture
) -> None:
    reg = stub_registry("import sys\nsys.stderr.write('STDERR-SENTINEL')\nprint('out')\n")
    c = TestClient(create_bridge_app(settings(), reg), base_url=f"http://127.0.0.1:{PORT}")
    with caplog.at_level(logging.DEBUG):
        r = run(c)
    assert "STDERR-SENTINEL" not in r.text
    assert "STDERR-SENTINEL" not in caplog.text


def test_cwd_gone_after_request(tmp_path: Path, stub_registry: Callable[..., Registry]) -> None:
    out = tmp_path / "cwd"
    reg = stub_registry(f"import os\nopen({str(out)!r}, 'w').write(os.getcwd())\n")
    c = TestClient(create_bridge_app(settings(), reg), base_url=f"http://127.0.0.1:{PORT}")
    run(c)
    assert not Path(out.read_text()).exists()


def test_injected_runner_used(stub_registry: Callable[..., Registry]) -> None:
    from bridge.runner import RunResult

    calls: list[str] = []

    def fake(command: Any, input_text: str, *, home: str, parent_env: Any) -> RunResult:
        calls.append(input_text)
        return RunResult(exit_code=0, stdout="fake", duration_ms=1)

    reg = stub_registry("pass\n")
    c = TestClient(
        create_bridge_app(settings(), reg, runner=fake), base_url=f"http://127.0.0.1:{PORT}"
    )
    assert run(c).json() == {"exit_code": 0, "stdout": "fake", "duration_ms": 1}
    assert calls == ["hi"]


def test_token_and_input_never_in_logs_or_errors(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    bodies = []
    with caplog.at_level(logging.DEBUG):
        bodies.append(run(client, json={"command": "stub", "input": 5, "x": SENTINEL}).text)
        bodies.append(run(client, json={"command": "bash", "input": SENTINEL}).text)
        bodies.append(run(client, json={"command": "stub", "input": SENTINEL * 50}).text)
        bodies.append(
            client.post(
                "/v1/run",
                content=SENTINEL,
                headers={**AUTH, "Content-Type": "text/plain"},
            ).text
        )
        bodies.append(
            client.post(
                "/v1/run",
                content=SENTINEL,
                headers={"Authorization": f"Bearer {TOKEN}x", "Content-Type": "application/json"},
            ).text
        )
    for text in (*bodies, caplog.text):
        assert TOKEN not in text
        assert SENTINEL not in text
