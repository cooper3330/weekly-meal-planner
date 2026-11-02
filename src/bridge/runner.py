"""Run one registered command: no shell, input on stdin only, scratch cwd, minimal env.

Timeout kill, output cap and the concurrency limit come in the next task (#118).
Neither stderr nor the input is ever returned, logged, or put in an exception message.
"""

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass

from bridge.registry import Command, build_child_env


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    duration_ms: int


class RunError(Exception):
    """A run that did not complete. `kind` is one of the contract's fixed error kinds."""

    def __init__(self, status: int, kind: str) -> None:
        super().__init__(kind)
        self.status = status
        self.kind = kind


def run_command(
    command: Command, input_text: str, *, home: str, parent_env: Mapping[str, str]
) -> RunResult:
    program = command.resolve_program(home)
    if not (program.is_file() and os.access(program, os.X_OK)):
        raise RunError(503, "program_not_found")

    try:
        data = input_text.encode("utf-8")
    except UnicodeEncodeError:
        raise RunError(500, "spawn_failed") from None

    scratch = tempfile.mkdtemp(prefix="mealplanner-bridge-")
    try:
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                [str(program), *command.args],
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=scratch,
                env=build_child_env(command, parent_env),
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            raise RunError(500, "spawn_failed") from None
        # communicate writes stdin, closes it, and drains both output pipes together, so the
        # child cannot block on a full pipe. stderr is discarded.
        stdout, _stderr = proc.communicate(input=data)
        return RunResult(
            exit_code=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
