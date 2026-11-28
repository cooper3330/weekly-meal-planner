"""Which changed paths make the CI ``e2e`` job run (#143, ADR 0028).

The job decides this itself, not through a workflow ``paths:`` filter: a filtered-out required
check would hang a pull request. Plain Python with no browser, so it is unit-tested in
``make check``. This module is a protected path: it decides whether a required gate runs.
"""

# Directories and files whose change can alter what the app does or what the browser run checks.
# `.github/workflows/ci.yml` is here so that a change to the job itself is exercised by the job.
RUNTIME_PREFIXES = ("src/", "tests/flows/", "tests/e2e/")
RUNTIME_FILES = frozenset(
    {"pyproject.toml", "uv.lock", "Makefile", ".github/workflows/ci.yml"},
)


def parse(text: str) -> list[str]:
    """Changed paths from ``git diff --name-only``: one per line, no blank lines or CRs."""
    return [line.strip("\r") for line in text.split("\n") if line.strip("\r").strip()]


def is_runtime_path(path: str) -> bool:
    return path in RUNTIME_FILES or path.startswith(RUNTIME_PREFIXES)


def in_scope(paths: list[str]) -> bool:
    """True when any changed path is a runtime path. An empty change set is out of scope."""
    return any(is_runtime_path(path) for path in paths)
