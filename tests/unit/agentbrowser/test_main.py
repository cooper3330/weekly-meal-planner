"""CLI tests for ``python -m agentbrowser`` (#100): state dir and start command are test doubles."""

import sys
from pathlib import Path

import pytest

from agentbrowser import __main__ as cli
from agentbrowser import lifecycle

STUB_OK = """
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""
STUB_EXIT = "import sys\nprint('stub boom', flush=True)\nsys.exit(3)\n"


def _use_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> Path:
    script = tmp_path / "stub.py"
    script.write_text(body)
    state = tmp_path / "state"
    monkeypatch.setattr(lifecycle, "STATE_DIR", state)
    monkeypatch.setattr(
        lifecycle,
        "demo_command",
        lambda port, data_dir, scenario: [
            sys.executable,
            "-u",
            str(script),
            str(port),
            str(data_dir),
        ],
    )
    return state


def test_up_prints_url_and_repeat_up_fails_naming_app_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_stub(monkeypatch, tmp_path, STUB_OK)
    try:
        assert cli.main(["up", "--name", "c1"]) == 0
        assert capsys.readouterr().out.strip().startswith("http://127.0.0.1:")
        assert cli.main(["up", "--name", "c1"]) == 1
        assert "make app-down APP_NAME=c1" in capsys.readouterr().err
    finally:
        assert cli.main(["down", "--name", "c1"]) == 0
    assert cli.main(["down", "--name", "c1"]) == 0


def test_failing_up_returns_1_with_log_tail_on_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _use_stub(monkeypatch, tmp_path, STUB_EXIT)
    assert cli.main(["up", "--name", "f"]) == 1
    captured = capsys.readouterr()
    assert "stub boom" in captured.err
    assert captured.out == ""
    assert not (state / "f.json").exists()


def test_down_with_no_state_returns_0(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use_stub(monkeypatch, tmp_path, STUB_OK)
    assert cli.main(["down", "--name", "nothing"]) == 0


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://example.com:8000", "http://127.0.0.1.evil.test"]
)
def test_browse_rejects_a_non_loopback_url_and_writes_the_reason(
    url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steps = tmp_path / "s.json"
    steps.write_text('{"steps": [{"goto": "/"}]}')
    out = tmp_path / "out"
    assert cli.main(["browse", "--steps", str(steps), "--url", url, "--out", str(out)]) == 1
    assert "must be http on 127.0.0.1" in capsys.readouterr().err
    assert "must be http on 127.0.0.1" in (out / "summary.md").read_text()


@pytest.mark.parametrize("url", ["http://127.0.0.1:1", "http://localhost:1", "http://[::1]:1"])
def test_loopback_urls_are_accepted(url: str) -> None:
    cli._check_loopback(url)
