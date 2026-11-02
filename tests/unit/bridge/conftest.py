"""Helpers for bridge tests: stub programs and a registry that points at them.

The real CLI is never started. Stubs are Python scripts with an absolute shebang.
"""

import json
import sys
import textwrap
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from bridge.registry import Registry, load_registry

TOKEN = "t" * 40
PORT = 8765


def write_stub(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def write_registry(
    tmp_path: Path,
    program: Path | str,
    *,
    args: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    passthrough: Sequence[str] = (),
    max_input_bytes: int = 1000,
) -> Registry:
    lines = [
        "[bridge]",
        "max_concurrent = 2",
        "[commands.stub]",
        f"program = {json.dumps(str(program))}",
        f"args = {json.dumps(list(args))}",
        "timeout_seconds = 10",
        f"max_input_bytes = {max_input_bytes}",
        "max_output_bytes = 100000",
        f"passthrough_env = {json.dumps(list(passthrough))}",
        "[commands.stub.env]",
    ]
    lines += [f"{k} = {json.dumps(v)}" for k, v in (env or {"FIXED": "1"}).items()]
    reg = tmp_path / "commands.toml"
    reg.write_text("\n".join(lines) + "\n")
    return load_registry(reg)


@pytest.fixture
def registry_for(tmp_path: Path) -> Callable[..., Registry]:
    """Build a registry for an existing program path (`write_registry` bound to tmp_path)."""

    def build(program: Path | str, **kwargs: Any) -> Registry:
        return write_registry(tmp_path, program, **kwargs)

    return build


@pytest.fixture
def stub_registry(tmp_path: Path) -> Callable[..., Registry]:
    """Build a registry whose single command `stub` runs `body` as a Python script."""

    def build(body: str, **kwargs: Any) -> Registry:
        program = write_stub(tmp_path / "stub.py", body)
        return write_registry(tmp_path, program, **kwargs)

    return build
