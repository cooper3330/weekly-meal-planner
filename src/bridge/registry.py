"""Command registry: what the bridge may run, and the environment it runs with.

Pure functions only: no HTTP, no process execution. Never imports `mealplanner`.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Defence in depth; the authoritative copy of these lists is in the test file.
_FORBIDDEN_ARGS = frozenset(
    {
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--allowedTools",
        "--allowed-tools",
        "--mcp-config",
        "--add-dir",
        "--plugin-dir",
        "--plugin-url",
        "--continue",
        "-c",
        "--resume",
        "-r",
        "--json-schema",
        "--bare",
        "--agents",
        "bypassPermissions",
        "acceptEdits",
        "auto",
    }
)
# Value flags a later occurrence could override.
_SINGLE_USE_FLAGS = frozenset(
    {
        "--tools",
        "--disallowedTools",
        "--settings",
        "--max-turns",
        "--model",
        "--output-format",
        "--input-format",
        "--system-prompt",
        "--permission-mode",
    }
)
_FORBIDDEN_PASSTHROUGH_PREFIXES = ("ANTHROPIC_", "MEALPLANNER_", "OTEL_")
_FORBIDDEN_PASSTHROUGH_NAMES = frozenset({"CLAUDE_CODE_ENABLE_TELEMETRY"})

_TOP_KEYS = {"bridge", "commands"}
_BRIDGE_KEYS = {"max_concurrent"}
_COMMAND_KEYS = {
    "program",
    "args",
    "timeout_seconds",
    "max_input_bytes",
    "max_output_bytes",
    "env",
    "passthrough_env",
}


class RegistryError(ValueError):
    """The registry file is invalid. Messages name keys, never environment values."""


@dataclass(frozen=True)
class Command:
    name: str
    program: str
    args: tuple[str, ...]
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int
    env: Mapping[str, str]
    passthrough_env: tuple[str, ...]

    def resolve_program(self, home: str) -> Path:
        """Expand a leading `~/` against `home` only. No $VAR expansion, no PATH search."""
        if self.program.startswith("~/"):
            return Path(home) / self.program[2:]
        if self.program.startswith("/"):
            return Path(self.program)
        raise RegistryError(f"command {self.name!r}: program must be absolute or start with '~/'")


@dataclass(frozen=True)
class Registry:
    max_concurrent: int
    commands: Mapping[str, Command]


def build_child_env(command: Command, parent_env: Mapping[str, str]) -> dict[str, str]:
    """The fixed env plus only the passthrough names present in `parent_env`. Nothing else."""
    env = {n: parent_env[n] for n in command.passthrough_env if n in parent_env}
    env.update(command.env)  # fixed values win, always
    return env


def _check_keys(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    for key in table:
        if key not in allowed:
            raise RegistryError(f"{where}: unknown key {key!r}")


def _positive_int(table: Mapping[str, Any], key: str, where: str) -> int:
    if key not in table:
        raise RegistryError(f"{where}: missing key {key!r}")
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistryError(f"{where}: {key} must be an integer")
    if value <= 0:
        raise RegistryError(f"{where}: {key} must be positive")
    return value


def _str_list(table: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    if key not in table:
        raise RegistryError(f"{where}: missing key {key!r}")
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RegistryError(f"{where}: {key} must be a list of strings")
    return tuple(value)


def _check_args(args: tuple[str, ...], where: str) -> None:
    seen: set[str] = set()
    for arg in args:
        parts = arg.split("=", 1) if arg.startswith("--") else [arg]
        for part in (arg, *parts):
            if part in _FORBIDDEN_ARGS:
                raise RegistryError(f"{where}: args contains a forbidden argument {part!r}")
        if parts[0] in _SINGLE_USE_FLAGS:
            if parts[0] in seen:
                raise RegistryError(f"{where}: args may contain {parts[0]} only once")
            seen.add(parts[0])


def _parse_command(name: object, table: object) -> Command:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise RegistryError("invalid command name (must match ^[a-z][a-z0-9_-]{0,63}$)")
    where = f"command {name!r}"
    if not isinstance(table, dict):
        raise RegistryError(f"{where}: must be a table")
    _check_keys(table, _COMMAND_KEYS, where)

    program = table.get("program")
    if not isinstance(program, str):
        raise RegistryError(f"{where}: program must be a string")
    if not (program.startswith("/") or program.startswith("~/")) or program in ("/", "~/"):
        raise RegistryError(f"{where}: program must be absolute or start with '~/'")

    args = _str_list(table, "args", where)
    _check_args(args, where)

    timeout = _positive_int(table, "timeout_seconds", where)
    max_in = _positive_int(table, "max_input_bytes", where)
    max_out = _positive_int(table, "max_output_bytes", where)

    env_raw = table.get("env", {})
    if not isinstance(env_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()
    ):
        raise RegistryError(f"{where}: env must be a table of strings")

    passthrough = _str_list(table, "passthrough_env", where)
    for pname in passthrough:
        if pname.startswith(_FORBIDDEN_PASSTHROUGH_PREFIXES) or (
            pname in _FORBIDDEN_PASSTHROUGH_NAMES
        ):
            raise RegistryError(f"{where}: passthrough_env may not include {pname}")
        if pname in env_raw:
            raise RegistryError(f"{where}: passthrough_env name {pname} is also a fixed env key")

    return Command(
        name=name,
        program=program,
        args=args,
        timeout_seconds=timeout,
        max_input_bytes=max_in,
        max_output_bytes=max_out,
        env=dict(env_raw),
        passthrough_env=passthrough,
    )


def load_registry(path: Path) -> Registry:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError:
        raise RegistryError(f"cannot read registry file {path.name}") from None
    except tomllib.TOMLDecodeError:
        raise RegistryError(f"registry file {path.name} is not valid TOML") from None

    _check_keys(data, _TOP_KEYS, "registry")
    bridge = data.get("bridge")
    if not isinstance(bridge, dict):
        raise RegistryError("registry: missing [bridge] table")
    _check_keys(bridge, _BRIDGE_KEYS, "bridge")
    max_concurrent = _positive_int(bridge, "max_concurrent", "bridge")

    cmds = data.get("commands")
    if not isinstance(cmds, dict) or not cmds:
        raise RegistryError("registry: missing [commands] table")
    commands = {name: _parse_command(name, table) for name, table in cmds.items()}
    return Registry(max_concurrent=max_concurrent, commands=commands)
