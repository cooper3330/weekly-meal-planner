"""Driver tests against fixture pages on a loopback server (#102).

Excluded from `make check` (needs Chromium; `make browser-install`). Run with:
`uv run pytest tests/e2e -o addopts=`
"""

import http.server
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentbrowser import __main__ as cli
from agentbrowser import lifecycle

SITE = Path(__file__).parent / "fixture_site"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)


def _png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


class _Server:
    def __init__(self, external: str = "") -> None:
        outer = self
        self.hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.hits.append(self.path)
                if self.path == "/slow.html":
                    time.sleep(8)
                if self.path == "/ok.png":
                    body, kind = PNG, "image/png"
                else:
                    page = SITE / self.path.lstrip("/")
                    if not page.is_file():
                        self.send_error(404)
                        return
                    body = page.read_text().replace("__EXTERNAL__", external).encode()
                    kind = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def outside() -> Iterator[_Server]:
    server = _Server()
    yield server
    server.close()


@pytest.fixture
def site(outside: _Server) -> Iterator[_Server]:
    server = _Server(external=outside.url)
    yield server
    server.close()


def _browse(
    tmp_path: Path, url: str, steps: list[dict[str, Any]], **extra: Any
) -> tuple[int, Path]:
    flow_file = tmp_path / "case.json"
    flow_file.write_text(json.dumps({"steps": steps, **extra}))
    out = tmp_path / "out"
    code = cli.main(["browse", "--steps", str(flow_file), "--url", url, "--out", str(out)])
    return code, out


def test_clean_page_writes_every_artifact_and_exits_0(
    site: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steps: list[dict[str, Any]] = [
        {"goto": "/clean.html"},
        {"expect_text": "Clean page", "shows": "AC-3"},
        {"fill": {"label": "Name", "value": "Ada"}},
        {"select": {"label": "Effort", "value": "slow"}},
        {"press": "Tab"},
        {"click": {"role": "button", "name": "Save"}},
        {"expect_text": "Saved it."},
        {"expect_no_text": "Nothing saved yet."},
        {"click": {"text": "Next page"}},
        {"expect_text": "Second page"},
    ]
    code, out = _browse(tmp_path, site.url, steps)
    assert code == 0, capsys.readouterr().err
    flow_dir = out / "case"
    kinds = [next(k for k in s if k != "shows") for s in steps]
    for number, kind in enumerate(kinds, 1):
        shot = flow_dir / f"{number:02d}-{kind}.png"
        assert shot.is_file() and 0 < shot.stat().st_size <= 1_000_000
    for name in ("final-full.png", "page.txt", "console.json", "requests.json", "trace.zip"):
        assert (flow_dir / name).is_file(), name
    assert "Second page" in (flow_dir / "page.txt").read_text()
    assert json.loads((flow_dir / "console.json").read_text()) == []
    requests = json.loads((flow_dir / "requests.json").read_text())
    assert {"url", "method", "status", "failed", "external"} <= set(requests[0])
    assert any(r["url"].endswith("/ok.png") and r["status"] == 200 for r in requests)
    assert not any(r["external"] or r["failed"] for r in requests)
    summary = json.loads((out / "summary.json").read_text())
    assert summary["flows"][0]["status"] == "pass"
    assert summary["flows"][0]["steps"][1]["shows"] == "AC-3"
    assert "PASS" in (out / "summary.md").read_text()
    # Phone-size defaults: 360 x 640 at device scale 2.
    assert _png_size(flow_dir / "01-goto.png") == (720, 1280)


def test_console_error_fails(
    site: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/console_error.html"}])
    assert code == 1
    assert "planted console error" in capsys.readouterr().err
    assert "planted console error" in (out / "summary.md").read_text()
    console = json.loads((out / "case" / "console.json").read_text())
    assert console[0]["type"] == "error"


def test_uncaught_exception_fails(
    site: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _browse(tmp_path, site.url, [{"goto": "/uncaught.html"}])
    assert code == 1
    assert "planted uncaught" in capsys.readouterr().err


def test_404_image_fails_and_is_named(
    site: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/bad_image.html"}])
    assert code == 1
    err = capsys.readouterr().err
    assert "/missing.png" in err and "404" in err
    requests = json.loads((out / "case" / "requests.json").read_text())
    assert any(r["url"].endswith("/missing.png") and r["status"] == 404 for r in requests)


def test_external_request_is_aborted_recorded_and_fails(
    site: _Server, outside: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/external.html"}])
    assert code == 1
    assert "external" in capsys.readouterr().err.lower()
    assert outside.hits == []
    requests = json.loads((out / "case" / "requests.json").read_text())
    external = [r for r in requests if r["external"]]
    assert [r["url"] for r in external] == [f"{outside.url}/outside.png"]


def test_missing_button_lists_what_the_page_offered(
    site: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steps = [{"goto": "/clean.html"}, {"click": {"role": "button", "name": "Delete everything"}}]
    code, out = _browse(tmp_path, site.url, steps)
    assert code == 1
    err = capsys.readouterr().err
    assert "step 2" in err and "click" in err
    assert "Delete everything" in err  # expected
    assert "Save" in err and "Next page" in err  # the page's buttons and links
    assert "step 2" in (out / "summary.md").read_text()
    assert (out / "case" / "02-click.png").is_file()
    assert (out / "case" / "trace.zip").is_file()


def test_failed_expectation_reports_expected_and_actual(
    site: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _browse(tmp_path, site.url, [{"goto": "/clean.html"}, {"expect_text": "Nope"}])
    assert code == 1
    err = capsys.readouterr().err
    assert "expected" in err and "Nope" in err and "actual" in err


def test_expect_no_text_fails_when_text_stays(
    site: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    steps = [{"goto": "/clean.html"}, {"expect_no_text": "Clean page"}]
    code, _ = _browse(tmp_path, site.url, steps)
    assert code == 1
    assert "Clean page" in capsys.readouterr().err


def test_invalid_flow_is_rejected_before_the_browser_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentbrowser import driver

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("browser started")

    monkeypatch.setattr(driver, "run_flow", boom)
    code, out = _browse(tmp_path, "http://127.0.0.1:9", [{"goto": "https://example.com"}])
    assert code == 1
    assert "goto" in capsys.readouterr().err
    summary = (out / "summary.md").read_text()
    assert "FAIL" in summary and "goto" in summary
    assert not (out / "case").exists()


def test_non_loopback_url_is_rejected_before_the_browser_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentbrowser import driver

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("browser started")

    monkeypatch.setattr(driver, "run_flow", boom)
    for url in ("https://example.com", "http://example.com:8000", "http://127.0.0.1.evil.test"):
        code, out = _browse(tmp_path, url, [{"goto": "/"}])
        assert code == 1
        assert "must be http on 127.0.0.1" in capsys.readouterr().err
        assert "FAIL" in (out / "summary.md").read_text()


def test_a_step_is_held_to_its_one_budget(
    site: _Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentbrowser import driver

    monkeypatch.setattr(driver, "STEP_TIMEOUT_MS", 700)
    started = time.monotonic()
    code, out = _browse(tmp_path, site.url, [{"goto": "/slow.html"}])
    assert code == 1
    assert time.monotonic() - started < 6  # the page takes 8 s
    assert "to load within 0.7 s" in (out / "summary.md").read_text()


def test_the_flow_is_held_to_its_deadline(
    site: _Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentbrowser import driver

    monkeypatch.setattr(driver, "FLOW_TIMEOUT_S", 1.0)
    started = time.monotonic()
    code, out = _browse(tmp_path, site.url, [{"goto": "/slow.html"}, {"goto": "/clean.html"}])
    assert code == 1
    assert time.monotonic() - started < 6
    assert "step 1 (goto) failed" in (out / "summary.md").read_text()


def test_steps_after_the_flow_deadline_are_not_run(
    site: _Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentbrowser import driver

    monkeypatch.setattr(driver, "FLOW_TIMEOUT_S", 0.001)
    code, out = _browse(tmp_path, site.url, [{"goto": "/clean.html"}, {"goto": "/clean.html"}])
    assert code == 1
    assert "not run: the flow took longer than" in (out / "summary.md").read_text()


def test_final_full_screenshot_is_held_to_one_megabyte(
    site: _Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentbrowser import driver

    monkeypatch.setattr(driver, "MAX_SCREENSHOT_BYTES", 1)
    code, out = _browse(tmp_path, site.url, [{"goto": "/clean.html"}])
    assert code == 1
    assert "final-full.png at most 1 bytes" in (out / "summary.md").read_text()


def test_viewport_comes_from_the_flow(site: _Server, tmp_path: Path) -> None:
    code, out = _browse(tmp_path, site.url, [{"goto": "/clean.html"}], viewport="412x915")
    assert code == 0
    assert _png_size(out / "case" / "01-goto.png") == (824, 1830)


def test_missing_browser_names_make_browser_install(
    site: _Server,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browsers"))
    code, _ = _browse(tmp_path, site.url, [{"goto": "/clean.html"}])
    assert code == 1
    assert "make browser-install" in capsys.readouterr().err


def test_url_defaults_to_the_app_state_file(
    site: _Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "mine.json").write_text(json.dumps({"url": site.url}))
    monkeypatch.setattr(lifecycle, "STATE_DIR", state)
    flow_file = tmp_path / "s.json"
    flow_file.write_text(json.dumps({"steps": [{"goto": "/clean.html"}]}))
    out = tmp_path / "o"
    assert cli.main(["browse", "--steps", str(flow_file), "--name", "mine", "--out", str(out)]) == 0


def test_no_app_up_and_no_url_is_a_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "STATE_DIR", tmp_path / "none")
    flow_file = tmp_path / "s.json"
    flow_file.write_text(json.dumps({"steps": [{"goto": "/"}]}))
    assert cli.main(["browse", "--steps", str(flow_file)]) == 1
    assert "make app-up" in capsys.readouterr().err


def test_demo_app_home_heading_and_identity_header(tmp_path: Path) -> None:
    """Against the real demo app: the driver supplies the owner identity; without it, 403."""
    from playwright.sync_api import sync_playwright

    handle = lifecycle.start("e2e-driver-test", "profile", state_dir=tmp_path / "state")
    try:
        code, _ = _browse(
            tmp_path,
            handle.url,
            [{"goto": "/"}, {"expect_text": "Weekly meal planner"}],
        )
        assert code == 0
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context().new_page()
            response = page.goto(handle.url + "/")
            assert response is not None and response.status == 403
            browser.close()
    finally:
        lifecycle.stop("e2e-driver-test", state_dir=tmp_path / "state")
