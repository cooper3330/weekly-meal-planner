"""Guard tests for the bridge command registry (#116, ADR 0022).

REQUIRED_ARGS, FORBIDDEN_ARGS and REQUIRED_ENV live here on purpose: loosening the registry
must not loosen the check in the same change.
"""

import json
from pathlib import Path

import pytest

from bridge.registry import Command, RegistryError, build_child_env, load_registry

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "src" / "bridge" / "commands.toml"

# Each entry is a tuple of adjacent args, in order.
REQUIRED_ARGS: list[tuple[str, ...]] = [
    ("-p",),
    ("--restricted",),
    ("--safe-mode",),
    ("--strict-mcp-config",),
    ("--tools", ""),
    ("--disallowedTools", "*"),
    ("--disable-slash-commands",),
    ("--no-session-persistence",),
    ("--permission-mode", "dontAsk"),
    ("--max-turns", "1"),
    ("--output-format", "json"),
    ("--input-format", "text"),
    ("--model", "sonnet"),
]

FORBIDDEN_ARGS = [
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
]

REQUIRED_ENV = [
    "DISABLE_TELEMETRY",
    "DISABLE_ERROR_REPORTING",
    "DISABLE_AUTOUPDATER",
    "DISABLE_GROWTHBOOK",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "DO_NOT_TRACK",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY",
    "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL",
    "CLAUDE_CODE_SKIP_PROMPT_HISTORY",
]

PASSTHROUGH = {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "CLAUDE_CONFIG_DIR"}


def claude() -> Command:
    return load_registry(REGISTRY_PATH).commands["claude"]


def has_sequence(args: tuple[str, ...], seq: tuple[str, ...]) -> bool:
    n = len(seq)
    return any(args[i : i + n] == seq for i in range(len(args) - n + 1))


def test_checked_in_registry_loads() -> None:
    reg = load_registry(REGISTRY_PATH)
    assert list(reg.commands) == ["claude"]
    assert reg.max_concurrent == 2
    cmd = reg.commands["claude"]
    assert cmd.program == "~/.local/bin/claude"
    assert cmd.timeout_seconds == 120
    assert cmd.max_input_bytes == 262144
    assert cmd.max_output_bytes == 1048576


@pytest.mark.parametrize("seq", REQUIRED_ARGS, ids=[" ".join(s) or "empty" for s in REQUIRED_ARGS])
def test_claude_args_disable_tools_and_files(seq: tuple[str, ...]) -> None:
    assert has_sequence(claude().args, seq)


def test_claude_args_settings_disable_hooks() -> None:
    args = claude().args
    i = args.index("--settings")
    assert json.loads(args[i + 1])["disableAllHooks"] is True


def test_claude_args_system_prompt_non_empty() -> None:
    args = claude().args
    i = args.index("--system-prompt")
    assert args[i + 1].strip()


@pytest.mark.parametrize("flag", FORBIDDEN_ARGS)
def test_claude_args_have_no_forbidden_flags(flag: str) -> None:
    parts: list[str] = []
    for arg in claude().args:
        parts.append(arg)
        if arg.startswith("--") and "=" in arg:
            parts.extend(arg.split("=", 1))
    assert flag not in parts


def test_claude_args_single_permission_mode() -> None:
    args = claude().args
    modes = [a for a in args if a == "--permission-mode" or a.startswith("--permission-mode=")]
    assert modes == ["--permission-mode"]


@pytest.mark.parametrize("name", REQUIRED_ENV)
def test_claude_env_disables_non_api_traffic(name: str) -> None:
    assert claude().env.get(name) == "1"


def test_claude_env_path_is_fixed() -> None:
    assert claude().env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"


def test_passthrough_list_exact() -> None:
    cmd = claude()
    assert set(cmd.passthrough_env) == PASSTHROUGH
    assert len(cmd.passthrough_env) == len(PASSTHROUGH)


def test_build_child_env_only_allowlisted() -> None:
    parent = {
        "ANTHROPIC_API_KEY": "sk-secret",
        "MEALPLANNER_BRIDGE_TOKEN": "tok",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x",
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "AWS_SECRET_ACCESS_KEY": "aws",
        "HOME": "/home/me",
        "PATH": "/evil/bin",
    }
    env = build_child_env(claude(), parent)
    assert env["HOME"] == "/home/me"
    assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    for name in REQUIRED_ENV:
        assert env[name] == "1"
    allowed = set(REQUIRED_ENV) | PASSTHROUGH | {"PATH"}
    assert set(env) <= allowed
    for bad in (
        "ANTHROPIC_API_KEY",
        "MEALPLANNER_BRIDGE_TOKEN",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "CLAUDE_CODE_ENABLE_TELEMETRY",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert bad not in env


def test_build_child_env_omits_absent_passthrough() -> None:
    env = build_child_env(claude(), {})
    assert "HOME" not in env
    assert env["DISABLE_TELEMETRY"] == "1"


@pytest.mark.parametrize("name", REQUIRED_ENV)
def test_parent_cannot_override_off_switch(name: str) -> None:
    assert build_child_env(claude(), {name: ""})[name] == "1"
    assert build_child_env(claude(), {name: "0"})[name] == "1"
    assert build_child_env(claude(), {})[name] == "1"


# ---- loader rejections ----

VALID_ARGS = '["-p"]'


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "commands.toml"
    p.write_text(text)
    return p


def toml(
    *,
    top: str = "",
    name: str = "claude",
    program: str = '"/usr/bin/true"',
    args: str = VALID_ARGS,
    timeout: str = "120",
    inp: str = "262144",
    out: str = "1048576",
    env: str = 'DISABLE_TELEMETRY = "1"',
    passthrough: str = '["HOME"]',
    extra: str = "",
    max_concurrent: str = "2",
) -> str:
    return f"""{top}
[bridge]
max_concurrent = {max_concurrent}

[commands."{name}"]
program = {program}
args = {args}
timeout_seconds = {timeout}
max_input_bytes = {inp}
max_output_bytes = {out}
passthrough_env = {passthrough}
{extra}

[commands."{name}".env]
{env}
"""


def test_valid_minimal_toml_loads(tmp_path: Path) -> None:
    reg = load_registry(write(tmp_path, toml()))
    assert "claude" in reg.commands


def test_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="surprise"):
        load_registry(write(tmp_path, toml(top="surprise = 1")))


def test_rejects_unknown_bridge_key(tmp_path: Path) -> None:
    text = toml().replace("max_concurrent = 2", "max_concurrent = 2\nsurprise = 1")
    with pytest.raises(RegistryError, match="surprise"):
        load_registry(write(tmp_path, text))


def test_rejects_unknown_command_key(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="surprise"):
        load_registry(write(tmp_path, toml(extra="surprise = 1")))


@pytest.mark.parametrize("name", ["Claude", "a b", "", "a" * 65, "1abc", "-x"])
def test_rejects_bad_command_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(RegistryError, match="command name"):
        load_registry(write(tmp_path, toml(name=name)))


def test_accepts_64_char_name(tmp_path: Path) -> None:
    load_registry(write(tmp_path, toml(name="a" * 64)))


@pytest.mark.parametrize("program", ['"claude"', '"$HOME/claude"', '"./claude"', '""'])
def test_rejects_bad_program(tmp_path: Path, program: str) -> None:
    with pytest.raises(RegistryError, match="program"):
        load_registry(write(tmp_path, toml(program=program)))


@pytest.mark.parametrize("field", ["timeout", "inp", "out", "max_concurrent"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_rejects_non_positive_limits(tmp_path: Path, field: str, value: str) -> None:
    with pytest.raises(RegistryError):
        load_registry(write(tmp_path, toml(**{field: value})))


@pytest.mark.parametrize("value", ['"120"', "true", "1.5"])
def test_rejects_non_integer_limits(tmp_path: Path, value: str) -> None:
    with pytest.raises(RegistryError, match="timeout_seconds"):
        load_registry(write(tmp_path, toml(timeout=value)))


@pytest.mark.parametrize(
    "name",
    [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "MEALPLANNER_BRIDGE_TOKEN",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "CLAUDE_CODE_ENABLE_TELEMETRY",
    ],
)
def test_rejects_forbidden_passthrough(tmp_path: Path, name: str) -> None:
    with pytest.raises(RegistryError, match=name):
        load_registry(write(tmp_path, toml(passthrough=f'["HOME", "{name}"]')))


def test_rejects_passthrough_duplicating_fixed_env(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="DISABLE_TELEMETRY"):
        load_registry(write(tmp_path, toml(passthrough='["DISABLE_TELEMETRY"]')))


@pytest.mark.parametrize("flag", FORBIDDEN_ARGS)
def test_rejects_forbidden_args(tmp_path: Path, flag: str) -> None:
    with pytest.raises(RegistryError, match="args"):
        load_registry(write(tmp_path, toml(args=json.dumps(["-p", flag]))))


def test_rejects_forbidden_arg_with_equals(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="args"):
        load_registry(write(tmp_path, toml(args=json.dumps(["-p", "--mcp-config=x.json"]))))


@pytest.mark.parametrize("mode", ["bypassPermissions", "acceptEdits", "auto"])
def test_rejects_forbidden_mode_as_equals_value(tmp_path: Path, mode: str) -> None:
    with pytest.raises(RegistryError, match="args"):
        load_registry(write(tmp_path, toml(args=json.dumps(["-p", f"--permission-mode={mode}"]))))


@pytest.mark.parametrize(
    "extra",
    [
        ["--permission-mode", "dontAsk"],
        ["--permission-mode=dontAsk"],
    ],
)
def test_rejects_repeated_permission_mode(tmp_path: Path, extra: list[str]) -> None:
    args = ["-p", "--permission-mode", "dontAsk", *extra]
    with pytest.raises(RegistryError, match="permission-mode"):
        load_registry(write(tmp_path, toml(args=json.dumps(args))))


def test_rejects_non_string_args(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="args"):
        load_registry(write(tmp_path, toml(args='["-p", 1]')))


def test_rejects_non_string_env_value(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="env"):
        load_registry(write(tmp_path, toml(env="DISABLE_TELEMETRY = 1")))


def test_rejects_missing_key(tmp_path: Path) -> None:
    text = toml().replace("timeout_seconds = 120\n", "")
    with pytest.raises(RegistryError, match="timeout_seconds"):
        load_registry(write(tmp_path, text))


def test_rejects_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        load_registry(write(tmp_path, "this is = = not toml"))


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "nope.toml")


def test_error_does_not_leak_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-very-secret")
    with pytest.raises(RegistryError) as exc:
        load_registry(write(tmp_path, toml(passthrough='["ANTHROPIC_API_KEY"]')))
    assert "sk-very-secret" not in str(exc.value)


# ---- resolve_program ----


def test_resolve_program_expands_home() -> None:
    assert claude().resolve_program("/h") == Path("/h/.local/bin/claude")


def test_resolve_program_absolute_unchanged(tmp_path: Path) -> None:
    reg = load_registry(write(tmp_path, toml(program='"/opt/x/claude"')))
    assert reg.commands["claude"].resolve_program("/h") == Path("/opt/x/claude")


def test_resolve_program_rejects_other_user_home(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="program"):
        load_registry(write(tmp_path, toml(program='"~user/x"')))


def test_resolve_program_does_not_expand_vars() -> None:
    cmd = Command(
        name="x",
        program="/opt/$HOME/claude",
        args=(),
        timeout_seconds=1,
        max_input_bytes=1,
        max_output_bytes=1,
        env={},
        passthrough_env=(),
    )
    assert cmd.resolve_program("/h") == Path("/opt/$HOME/claude")
