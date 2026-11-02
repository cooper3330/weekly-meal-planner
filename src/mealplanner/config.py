"""Settings loaded from the environment. The only module that reads ``os.environ``."""

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_DATA_DIR = "./data"
DEFAULT_MODEL = "claude-sonnet-4-5"


class ConfigError(ValueError):
    """The environment holds an invalid or unsafe setting."""


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    model: str
    allowed_hosts: tuple[str, ...]
    # Never shown in repr, so it cannot leak through logs or tracebacks.
    anthropic_api_key: str | None = field(default=None, repr=False, compare=False)
    owner_login: str | None = None
    dev_login: str | None = None
    bridge_host: str = DEFAULT_HOST
    bridge_port: int | None = None
    # Never shown in repr or compared, like the API key.
    bridge_token: str | None = field(default=None, repr=False, compare=False)


def normalize_login(raw: str | None) -> str | None:
    """Trim and lowercase a Tailscale login; blank becomes None."""
    if raw is None:
        return None
    return raw.strip().lower() or None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_port(raw: str, name: str = "MEALPLANNER_PORT") -> int:
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"{name} must be between 1 and 65535, got {port}")
    return port


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``env`` (defaults to the process environment)."""
    if env is None:
        env = os.environ
    host = env.get("MEALPLANNER_HOST", DEFAULT_HOST).strip()
    if not _is_loopback(host):
        raise ConfigError(
            f"MEALPLANNER_HOST must be a loopback address (127.0.0.1, ::1, localhost), "
            f"got {host!r}. Expose the app with `tailscale serve`, not by binding wider."
        )
    port = _parse_port(env.get("MEALPLANNER_PORT", str(DEFAULT_PORT)))
    allowed = tuple(
        item.strip() for item in env.get("MEALPLANNER_ALLOWED_HOSTS", "").split(",") if item.strip()
    )
    bridge_host = env.get("MEALPLANNER_BRIDGE_HOST", DEFAULT_HOST).strip()
    if not _is_loopback(bridge_host):
        raise ConfigError(
            f"MEALPLANNER_BRIDGE_HOST must be a loopback address (127.0.0.1, ::1, localhost), "
            f"got {bridge_host!r}."
        )
    # A missing port or token is not an error here; it surfaces when a model call is made.
    raw_bridge_port = env.get("MEALPLANNER_BRIDGE_PORT", "").strip()
    bridge_port = (
        _parse_port(raw_bridge_port, "MEALPLANNER_BRIDGE_PORT") if raw_bridge_port else None
    )
    return Settings(
        bridge_host=bridge_host,
        bridge_port=bridge_port,
        bridge_token=env.get("MEALPLANNER_BRIDGE_TOKEN", "").strip() or None,
        host=host,
        port=port,
        data_dir=Path(env.get("MEALPLANNER_DATA_DIR", DEFAULT_DATA_DIR)),
        model=env.get("MEALPLANNER_MODEL", DEFAULT_MODEL),
        allowed_hosts=allowed,
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip() or None,
        owner_login=normalize_login(env.get("MEALPLANNER_OWNER_LOGIN")),
        dev_login=normalize_login(env.get("MEALPLANNER_DEV_LOGIN")),
    )
