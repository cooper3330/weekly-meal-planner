"""`make bridge-smoke`: the owner-run check against the real CLI (#112 AC-13 gate).

Runs the registered `claude` command once, directly through `run_command` (no HTTP), with a
tiny fixed prompt. Not part of `make check`. Prints no environment values and no path.
"""

import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from bridge.config import load_parent_env, resolve_home
from bridge.registry import Registry, build_child_env, load_registry
from bridge.runner import RunError, run_command

COMMAND = "claude"
PROMPT = 'Reply with exactly this JSON object and nothing else: {"ok": true}'
REGISTRY_PATH = Path(__file__).resolve().parent / "commands.toml"


def _version(registry: Registry, home: str, parent_env: Mapping[str, str]) -> str:
    command = registry.commands[COMMAND]
    program = command.resolve_program(home)
    try:
        with tempfile.TemporaryDirectory(prefix="mealplanner-bridge-") as scratch:
            proc = subprocess.run(
                [str(program), "--version"],
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                cwd=scratch,
                env=build_child_env(command, parent_env),
                timeout=30,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return proc.stdout.decode("utf-8", errors="replace").strip() or "unavailable"


def run_smoke(registry: Registry, *, home: str, parent_env: Mapping[str, str]) -> int:
    command = registry.commands[COMMAND]
    print(f"cli version: {_version(registry, home, parent_env)} (minimum 2.1.248)")
    try:
        result = run_command(command, PROMPT, home=home, parent_env=parent_env)
    except RunError as exc:
        print(f"run failed: {exc.kind}")
        return 1

    envelope: object = None
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    envelope_ok = isinstance(envelope, dict)
    is_error = envelope.get("is_error") if isinstance(envelope, dict) else None

    result_ok = False
    if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
        text = envelope["result"].strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            result_ok = json.loads(text) == {"ok": True}
        except json.JSONDecodeError:
            pass

    print(f"exit code: {result.exit_code}")
    print(f"envelope parsed: {envelope_ok}")
    print(f"is_error: {is_error}")
    print(f"result is the expected object: {result_ok}")
    passed = result.exit_code == 0 and envelope_ok and is_error is False and result_ok
    print("SMOKE PASSED" if passed else "SMOKE FAILED: the bridge does not ship (#112 AC-13)")
    return 0 if passed else 1


def main() -> int:
    parent_env = load_parent_env()
    home = resolve_home(parent_env)
    return run_smoke(load_registry(REGISTRY_PATH), home=home, parent_env=parent_env)


if __name__ == "__main__":
    sys.exit(main())
