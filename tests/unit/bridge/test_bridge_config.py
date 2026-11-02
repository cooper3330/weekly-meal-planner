"""Bridge settings (#117, AC-2): loopback IP literal only, token and port required."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bridge.config import BridgeConfigError, load_bridge_settings

TOKEN = "s3cret-" + "x" * 40


def env(**overrides: str) -> dict[str, str]:
    base = {"MEALPLANNER_BRIDGE_PORT": "8000", "MEALPLANNER_BRIDGE_TOKEN": TOKEN}
    base.update(overrides)
    return base


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::", "example.com", "localhost", ""])
def test_non_loopback_host_rejected(host: str) -> None:
    with pytest.raises(BridgeConfigError) as exc:
        load_bridge_settings(env(MEALPLANNER_BRIDGE_HOST=host))
    assert "MEALPLANNER_BRIDGE_HOST" in str(exc.value)
    if host not in ("", "::"):  # "::" is a substring of the allowed literal "::1"
        assert host not in str(exc.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_accepted(host: str) -> None:
    assert load_bridge_settings(env(MEALPLANNER_BRIDGE_HOST=host)).host == host


def test_defaults_and_fields() -> None:
    s = load_bridge_settings(env(OTHER="y"))
    assert (s.host, s.port, s.token) == ("127.0.0.1", 8000, TOKEN)
    assert s.parent_env["OTHER"] == "y"


@pytest.mark.parametrize("token", [None, "", "short" * 6])
def test_missing_or_short_token(token: str | None) -> None:
    e = env()
    del e["MEALPLANNER_BRIDGE_TOKEN"]
    if token is not None:
        e["MEALPLANNER_BRIDGE_TOKEN"] = token
    with pytest.raises(BridgeConfigError) as exc:
        load_bridge_settings(e)
    assert "MEALPLANNER_BRIDGE_TOKEN" in str(exc.value)
    if token:
        assert token not in str(exc.value)


@pytest.mark.parametrize("port", [None, "", "abc", "0", "65536", "-1", "80.5"])
def test_bad_port(port: str | None) -> None:
    e = env()
    del e["MEALPLANNER_BRIDGE_PORT"]
    if port is not None:
        e["MEALPLANNER_BRIDGE_PORT"] = port
    with pytest.raises(BridgeConfigError) as exc:
        load_bridge_settings(e)
    assert "MEALPLANNER_BRIDGE_PORT" in str(exc.value)


def test_port_bounds_accepted() -> None:
    assert load_bridge_settings(env(MEALPLANNER_BRIDGE_PORT="1")).port == 1
    assert load_bridge_settings(env(MEALPLANNER_BRIDGE_PORT="65535")).port == 65535


def test_repr_hides_token() -> None:
    assert TOKEN not in repr(load_bridge_settings(env()))


def test_defaults_to_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALPLANNER_BRIDGE_PORT", "9000")
    monkeypatch.setenv("MEALPLANNER_BRIDGE_TOKEN", TOKEN)
    assert load_bridge_settings().port == 9000


def test_main_refuses_non_loopback_host() -> None:
    src = Path(__file__).resolve().parents[3] / "src"
    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(src),
        "MEALPLANNER_BRIDGE_HOST": "0.0.0.0",
        "MEALPLANNER_BRIDGE_PORT": "8000",
        "MEALPLANNER_BRIDGE_TOKEN": TOKEN,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "bridge"],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 2
    out = proc.stdout + proc.stderr
    assert TOKEN not in out
    assert "0.0.0.0" not in out
    assert "MEALPLANNER_BRIDGE_HOST" in out
