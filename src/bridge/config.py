"""Bridge settings. The only module in `bridge/` that reads `os.environ`.

Error messages name the variable, never its value (the token and the host must not leak).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

HOST_VAR = "MEALPLANNER_BRIDGE_HOST"
PORT_VAR = "MEALPLANNER_BRIDGE_PORT"
TOKEN_VAR = "MEALPLANNER_BRIDGE_TOKEN"
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
MIN_TOKEN_CHARS = 32


class BridgeConfigError(ValueError):
    """Bridge settings are missing or unsafe."""


@dataclass(frozen=True)
class BridgeSettings:
    host: str
    port: int
    token: str = field(repr=False)
    # A snapshot, used as the source for the registry's passthrough names and for HOME.
    parent_env: Mapping[str, str] = field(repr=False, default_factory=dict)


def load_parent_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def load_bridge_settings(env: Mapping[str, str] | None = None) -> BridgeSettings:
    parent_env = load_parent_env(env)

    host = parent_env.get(HOST_VAR, "127.0.0.1")
    if host not in LOOPBACK_HOSTS:
        raise BridgeConfigError(f"{HOST_VAR} must be the loopback IP literal 127.0.0.1 or ::1")

    raw_port = parent_env.get(PORT_VAR, "")
    if not raw_port.isascii() or not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
        raise BridgeConfigError(f"{PORT_VAR} is required and must be a number from 1 to 65535")

    token = parent_env.get(TOKEN_VAR, "")
    if len(token) < MIN_TOKEN_CHARS:
        raise BridgeConfigError(f"{TOKEN_VAR} is required and must be at least 32 characters")

    return BridgeSettings(host=host, port=int(raw_port), token=token, parent_env=parent_env)
