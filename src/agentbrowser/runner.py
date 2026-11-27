"""``python -m agentbrowser e2e``: run every flow in ``tests/flows/`` against a fresh demo app.

Each flow gets its own app (started and stopped through the lifecycle module, whatever happens)
and all flows share one browser. A failure prints what failed and ends with the one command that
reproduces it. There is no retry: a flow that fails once has failed.
"""

import os
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbrowser import lifecycle
from agentbrowser.evidence import FlowResult, write_summary
from agentbrowser.flow import Flow, FlowError, load

FLOWS_DIR = lifecycle.REPO_ROOT / "tests" / "flows"
RUN_ROOT = lifecycle.REPO_ROOT / ".agent-tmp" / "e2e"
WIRED_MESSAGE = "wired mode arrives in #145"


def _reproduce(path: Path) -> str:
    try:
        shown = path.resolve().relative_to(lifecycle.REPO_ROOT)
    except ValueError:
        shown = path
    return f"make e2e FLOW={shown}"


def _discover(flows_dir: Path) -> list[Path]:
    return sorted(flows_dir.glob("*.json")) if flows_dir.is_dir() else []


def _is_in(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _failed(path: Path, reason: str) -> FlowResult:
    return FlowResult(path.stem, [], failures=[reason], reproduce=_reproduce(path))


def _report(result: FlowResult, out: Path) -> None:
    print(f"FAIL {result.name}", file=sys.stderr)
    for reason in result.failures:
        print(f"- {reason}", file=sys.stderr)
    print(f"Artifacts: {out / result.name}", file=sys.stderr)
    print(f"Reproduce: {result.reproduce}", file=sys.stderr)


def _run_one(
    path: Path,
    flow: Flow,
    index: int,
    out: Path,
    browser: Any,
    lifecycle_api: Any,
    driver_api: Any,
) -> FlowResult:
    name = f"e2e-{os.getpid()}-{index}"
    try:
        try:
            handle = lifecycle_api.start(name, flow.scenario, flow.mode)
        except lifecycle.LifecycleError as exc:
            return _failed(path, str(exc))
        result: FlowResult = driver_api.run_flow(
            flow,
            handle.url,
            out / path.stem,
            name=path.stem,
            browser=browser,
            reproduce=_reproduce(path),
        )
        return result
    finally:
        lifecycle_api.stop(name)


def run(
    flow: Path | None,
    out: Path,
    *,
    flows_dir: Path | None = None,
    lifecycle_api: Any = lifecycle,
    driver_api: Any = None,
) -> int:
    """Run the flows and return the exit status. ``driver_api`` is the driver module."""
    directory = flows_dir if flows_dir is not None else FLOWS_DIR
    if flow is not None:
        if not flow.is_file():
            print(f"Flow file not found: {flow}", file=sys.stderr)
            return 2
        paths = [flow]
    else:
        paths = _discover(directory)
    if not paths:
        print("no flows")
        return 0

    results: list[FlowResult] = []
    runnable: list[tuple[Path, Flow]] = []
    for path in paths:
        try:
            loaded = load(path, strict=_is_in(path, directory))
        except FlowError as exc:
            results.append(_failed(path, str(exc)))
            continue
        if loaded.mode == "wired":
            results.append(_failed(path, f"{path.name}: {WIRED_MESSAGE}"))
            continue
        runnable.append((path, loaded))

    try:
        if runnable:
            if driver_api is None:
                from agentbrowser import driver as driver_api  # needs Playwright
            with ExitStack() as stack:
                try:
                    browser = stack.enter_context(driver_api.open_browser())
                except driver_api.BrowserMissingError as exc:
                    print(exc, file=sys.stderr)
                    return 1
                for index, (path, loaded) in enumerate(runnable):
                    results.append(
                        _run_one(path, loaded, index, out, browser, lifecycle_api, driver_api)
                    )
    finally:
        if results:
            write_summary(out, results)

    failed = [r for r in results if not r.ok]
    print(f"{len(results) - len(failed)}/{len(results)} flows passed", flush=True)
    for result in results:
        if result.ok:
            print(f"PASS {result.name}")
    for result in failed:
        _report(result, out)
    if failed:
        return 1
    print(f"Evidence: {out}")
    return 0


def default_out() -> Path:
    return RUN_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
