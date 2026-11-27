"""Stdin lifeline: when the parent closes our stdin, terminate ourselves (#120).

The supervisor holds the write end of a pipe on our stdin. However it dies (even SIGKILL),
the kernel closes the pipe, the read hits EOF, and this process gets SIGTERM. Works on macOS
and Linux. `mealplanner` has its own copy; the two packages never import each other.

The thread reads the raw file descriptor, not `sys.stdin`: a daemon thread blocked inside the
buffered reader would abort the interpreter at a normal shutdown.
"""

import os
import signal
import threading


def _wait_for_eof(fd: int) -> None:
    try:
        while os.read(fd, 4096):
            pass
    except OSError:
        pass  # a broken or closed pipe means the parent is gone too
    os.kill(os.getpid(), signal.SIGTERM)


def start_lifeline(fd: int = 0) -> threading.Thread:
    """Start the daemon thread that reads `fd` (stdin) to EOF, then SIGTERMs this process."""
    thread = threading.Thread(target=_wait_for_eof, args=(fd,), name="lifeline", daemon=True)
    thread.start()
    return thread
