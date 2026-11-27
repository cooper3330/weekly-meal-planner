import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mealplanner.app as app_module
import mealplanner.demo.__main__ as entry
from mealplanner.app import DB_FILENAME
from mealplanner.demo import DEMO_OWNER, FIXED_NOW, DemoError
from mealplanner.demo.build import build_demo_app, seed_scenario
from mealplanner.demo.client import DemoLLMClient

HEADERS = {"Tailscale-User-Login": DEMO_OWNER}


def make(data_dir: Path, scenario: str = "profile") -> TestClient:
    return TestClient(build_demo_app(data_dir, scenario), base_url="http://localhost")


@pytest.mark.parametrize("path", ["/", "/healthz", "/profile"])
def test_pages_render_with_demo_header(tmp_path: Path, path: str) -> None:
    with make(tmp_path) as client:
        assert client.get(path, headers=HEADERS).status_code == 200


@pytest.mark.parametrize("path", ["/", "/healthz", "/profile"])
def test_pages_forbidden_without_header(tmp_path: Path, path: str) -> None:
    with make(tmp_path) as client:
        assert client.get(path).status_code == 403
        other = {"Tailscale-User-Login": "someone@example.test"}
        assert client.get(path, headers=other).status_code == 403


def test_profile_scenario_shows_fixture_profile(tmp_path: Path) -> None:
    with make(tmp_path, "profile") as client:
        text = client.get("/profile", headers=HEADERS).text
    assert "You have not set up your profile yet" not in text
    assert "Peanuts" in text
    assert "Likes" in text
    assert "Dislikes" in text


def test_empty_scenario_shows_no_profile(tmp_path: Path) -> None:
    with make(tmp_path, "empty") as client:
        text = client.get("/profile", headers=HEADERS).text
    assert "You have not set up your profile yet" in text


def test_unknown_scenario_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(DemoError, match="nope.*empty.*profile"):
        build_demo_app(tmp_path, "nope")


def test_unknown_scenario_exits_from_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        entry.main(["--port", "8123", "--data-dir", str(tmp_path), "--scenario", "nope"])
    assert exc.value.code != 0
    assert "nope" in capsys.readouterr().err


def test_clock_is_fixed_and_aware(tmp_path: Path) -> None:
    app = build_demo_app(tmp_path, "empty")
    now = app.state.clock()
    assert now == datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    assert now.tzinfo is not None
    assert now == FIXED_NOW
    assert now.weekday() == 0


def test_app_uses_demo_client_and_owner(tmp_path: Path) -> None:
    app = build_demo_app(tmp_path, "empty")
    assert isinstance(app.state.llm_client, DemoLLMClient)
    assert app.state.settings.owner_login == "demo-owner@example.test"
    assert app.state.settings.dev_login is None
    assert app.state.settings.host == "127.0.0.1"


@pytest.mark.parametrize("env", [False, True])
def test_bridge_client_never_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: bool
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("CLIBridgeLLMClient constructed in demo mode")

    monkeypatch.setattr(app_module, "CLIBridgeLLMClient", boom)
    if env:
        monkeypatch.setenv("MEALPLANNER_BRIDGE_TOKEN", "secret-token")
        monkeypatch.setenv("MEALPLANNER_BRIDGE_PORT", "9999")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    with make(tmp_path) as client:
        assert client.get("/", headers=HEADERS).status_code == 200


def test_data_dir_created_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    build_demo_app(target, "empty")
    assert target.is_dir()


def test_seeding_twice_does_not_duplicate(tmp_path: Path) -> None:
    build_demo_app(tmp_path, "profile")
    build_demo_app(tmp_path, "profile")
    seed_scenario(tmp_path / DB_FILENAME, "profile")
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    try:
        assert conn.execute("SELECT COUNT(*) FROM profile_versions").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("port", ["0", "70000", "-1"])
def test_bad_port_refused(tmp_path: Path, port: str) -> None:
    with pytest.raises(SystemExit) as exc:
        entry.main(["--port", port, "--data-dir", str(tmp_path), "--scenario", "empty"])
    assert exc.value.code != 0


def test_non_loopback_host_refused(tmp_path: Path) -> None:
    argv = ["--host", "0.0.0.0", "--port", "8123", "--data-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as exc:
        entry.main([*argv, "--scenario", "empty"])
    assert exc.value.code != 0


def test_main_serves_on_loopback_and_starts_lifeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: calls.update(kw))
    monkeypatch.setattr(entry, "start_lifeline", lambda: calls.update(lifeline=True))
    argv = ["--port", "8123", "--data-dir", str(tmp_path), "--scenario", "empty"]
    entry.main([*argv, "--lifeline-stdin"])
    assert calls == {"host": "127.0.0.1", "port": 8123, "lifeline": True}
