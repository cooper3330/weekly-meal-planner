"""Run one registered command: no shell, input on stdin only, scratch cwd, minimal env.

The timeout and the output cap kill the whole process group. The concurrency limit and the
audit line live in the app. Neither stderr nor the input is ever returned, logged, or put in an
exception message.
"""

import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass

from bridge.registry import Command, build_child_env

STDERR_CAP_BYTES = 64 * 1024
_CHUNK = 65536
_POLL_SECONDS = 0.05


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


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _exited(proc: subprocess.Popen[bytes]) -> bool:
    """True once the child has exited; reaps it only where waitid/WNOWAIT is unavailable.

    os.waitid and os.WNOWAIT are not guaranteed everywhere (notably macOS), so fall back to
    poll(). A reap is safe: a group id stays reserved while any member lives, and the group
    kill that follows only matters when members remain.
    """
    waitid = getattr(os, "waitid", None)
    wnowait = getattr(os, "WNOWAIT", None)
    if waitid is None or wnowait is None:
        return proc.poll() is not None
    try:
        return waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | wnowait) is not None
    except ChildProcessError:
        return True


def _pump(proc: subprocess.Popen[bytes], data: bytes, deadline: float, max_out: int) -> bytes:
    """Feed stdin and read stdout and stderr incrementally.

    Raises RunError for a timeout or an over-cap stdout; the caller kills the group.
    """
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    sel = selectors.DefaultSelector()
    stdout_buf = bytearray()
    stderr_kept = 0
    view = memoryview(data)
    offset = 0
    stdin_fd = proc.stdin.fileno()
    os.set_blocking(stdin_fd, False)
    if data:
        sel.register(stdin_fd, selectors.EVENT_WRITE, "in")
    else:
        proc.stdin.close()
    sel.register(proc.stdout.fileno(), selectors.EVENT_READ, "out")
    sel.register(proc.stderr.fileno(), selectors.EVENT_READ, "err")
    group_killed = False
    try:
        while any(key.data != "in" for key in sel.get_map().values()):
            if time.monotonic() >= deadline:
                raise RunError(504, "timeout")
            if not group_killed and _exited(proc):
                # The leader is done. Whatever it left running must not outlive the run or hold
                # the pipes open; reading continues until EOF.
                _kill_group(proc.pid)
                group_killed = True
            for key, _ in sel.select(_POLL_SECONDS):
                fd = key.fd
                if key.data == "in":
                    try:
                        offset += os.write(fd, view[offset : offset + _CHUNK])
                    except BlockingIOError:
                        continue
                    except OSError:
                        offset = len(data)
                    if offset >= len(data):
                        sel.unregister(fd)
                        proc.stdin.close()
                    continue
                chunk = os.read(fd, _CHUNK)
                if not chunk:
                    sel.unregister(fd)
                elif key.data == "out":
                    stdout_buf += chunk
                    if len(stdout_buf) > max_out:
                        raise RunError(502, "output_too_large")
                else:
                    # Drained so the child never blocks; discarded. Exceeding the cap is not an
                    # error, we just stop counting.
                    stderr_kept = min(stderr_kept + len(chunk), STDERR_CAP_BYTES)
        return bytes(stdout_buf)
    finally:
        sel.close()


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
    proc: subprocess.Popen[bytes] | None = None
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
        stdout = _pump(proc, data, started + command.timeout_seconds, command.max_output_bytes)
        # The pipes can close while the leader keeps running; bound this wait by the deadline.
        try:
            proc.wait(timeout=max(0.0, started + command.timeout_seconds - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise RunError(504, "timeout") from None
        return RunResult(
            exit_code=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        if proc is not None:
            # Every exit path: nothing the command started may outlive the run.
            _kill_group(proc.pid)
            proc.wait()
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        shutil.rmtree(scratch, ignore_errors=True)
