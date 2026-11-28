"""``python -m agentbrowser e2e``: run every flow in ``tests/flows/``, then walk the feature map.

Each flow gets its own app (started and stopped through the lifecycle module, whatever happens)
and all flows share one browser. After the flows, every entry of the feature map (ADR 0029) is
walked from the home page, each in a fresh app, by its own reach steps. A failure prints what
failed and ends with the one command that reproduces it. There is no retry: a flow or feature
that fails once has failed.
"""

import os
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbrowser import feature_map, lifecycle
from agentbrowser.evidence import Budget, CutOff, FlowResult, WalkResult, write_summary
from agentbrowser.flow import Flow, FlowError, coverage, coverage_lines, load

FLOWS_DIR = lifecycle.REPO_ROOT / "tests" / "flows"
DEFAULT_MAP = lifecycle.REPO_ROOT / "tests" / "e2e" / "feature-map.json"
MAP_PATH = DEFAULT_MAP  # read at call time, like FLOWS_DIR
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


def _report_walk(walk: WalkResult, out: Path) -> None:
    print(f"FAIL feature {walk.feature}", file=sys.stderr)
    for reason in walk.failures:
        print(f"- {reason}", file=sys.stderr)
    print(f"Artifacts: {out / 'walk' / walk.feature}", file=sys.stderr)
    print(f"Reproduce: {walk.result.reproduce}", file=sys.stderr)


def _run_one(
    path: Path,
    flow: Flow,
    index: int,
    out: Path,
    browser: Any,
    lifecycle_api: Any,
    driver_api: Any,
    budget: Budget,
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
            budget=budget,
        )
        return result
    finally:
        lifecycle_api.stop(name)


def _walk_one(
    fmap: feature_map.FeatureMap,
    feature: feature_map.Feature,
    index: int,
    out: Path,
    browser: Any,
    lifecycle_api: Any,
    driver_api: Any,
    budget: Budget,
) -> WalkResult:
    name = f"e2e-{os.getpid()}-walk-{index}"
    reproduce = f"make e2e FEATURE={feature.name}"
    near = feature_map.neighbours(fmap, feature.name)
    flow = feature_map.to_walk_flow(feature)
    try:
        try:
            handle = lifecycle_api.start(name, flow.scenario, flow.mode)
        except lifecycle.LifecycleError as exc:
            failed = FlowResult(feature.name, [], failures=[str(exc)], reproduce=reproduce)
            return WalkResult(feature.name, failed, near)
        result: FlowResult = driver_api.run_flow(
            flow,
            handle.url,
            out / "walk" / feature.name,
            name=feature.name,
            browser=browser,
            reproduce=reproduce,
            rel_dir=f"walk/{feature.name}",
            path_check=feature_map.path_check(feature),
            budget=budget,
        )
        return WalkResult(feature.name, result, near)
    finally:
        lifecycle_api.stop(name)


def run(
    flow: Path | None,
    out: Path,
    *,
    flows_dir: Path | None = None,
    lifecycle_api: Any = lifecycle,
    driver_api: Any = None,
    map_path: Path | None = None,
    feature: str | None = None,
    budget: Budget | None = None,
) -> int:
    """Run the flows, then the walk, and return the exit status.

    The run has one budget (ADR 0030): 10 minutes, 200 screenshots and 30 MB of PNG bytes. When a
    limit is reached no further flow or step starts, ``summary.json`` says why, and the status is
    non-zero. ``budget`` is for tests (a fake clock).

    ``flow`` runs only that flow (no walk); ``feature`` walks only that feature (no flows).
    ``driver_api`` is the driver module; ``map_path`` defaults to ``MAP_PATH``.
    """
    directory = flows_dir if flows_dir is not None else FLOWS_DIR
    if flow is not None and feature is not None:
        print("--flow and --feature cannot be used together.", file=sys.stderr)
        return 2
    fmap: feature_map.FeatureMap | None = None
    paths: list[Path]
    if flow is not None:
        if not flow.is_file():
            print(f"Flow file not found: {flow}", file=sys.stderr)
            return 2
        paths = [flow]
        walked: list[feature_map.Feature] = []
    else:
        try:
            fmap = feature_map.load(map_path if map_path is not None else MAP_PATH)
        except feature_map.FeatureMapError as exc:
            print(exc, file=sys.stderr)
            return 1
        walked = sorted(fmap.features, key=lambda f: f.name)
        if feature is not None:
            walked = [f for f in walked if f.name == feature]
            if not walked:
                known = ", ".join(f.name for f in fmap.features) or "none"
                print(f"Unknown feature {feature!r}. Known features: {known}", file=sys.stderr)
                return 2
            paths = []
        else:
            paths = _discover(directory)
    if not paths and not walked:
        print("no flows")
        return 0

    budget = budget or Budget()
    results: list[FlowResult] = []
    walks: list[WalkResult] = []
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

    declared = coverage({p.stem: f for p, f in runnable})
    if declared:
        print("Coverage:")
        for line in coverage_lines(declared):
            print(f"  {line}")
        print(flush=True)

    try:
        if runnable or walked:
            if driver_api is None:
                from agentbrowser import driver as driver_api  # needs Playwright
            with ExitStack() as stack:
                try:
                    browser = stack.enter_context(driver_api.open_browser())
                except driver_api.BrowserMissingError as exc:
                    print(exc, file=sys.stderr)
                    return 1
                for index, (path, loaded) in enumerate(runnable):
                    if budget.check() is not None:
                        break
                    results.append(
                        _run_one(
                            path, loaded, index, out, browser, lifecycle_api, driver_api, budget
                        )
                    )
                for index, item in enumerate(walked):
                    assert fmap is not None
                    if budget.check() is not None:
                        break
                    walks.append(
                        _walk_one(
                            fmap, item, index, out, browser, lifecycle_api, driver_api, budget
                        )
                    )
    finally:
        # A limit reached while the last flow ran is seen here, with no flow left to check it.
        limit = budget.cut_off
        cut_off = CutOff(limit, len(results) + len(walks)) if limit else None
        if results or walks:
            write_summary(
                out,
                results,
                head_sha=os.environ.get("AGENTBROWSER_HEAD_SHA") or None,
                coverage=declared,
                walk=walks,
                cut_off=cut_off,
            )

    failed = [r for r in results if not r.ok]
    failed_walks = [w for w in walks if not w.ok]
    if results:
        print(f"{len(results) - len(failed)}/{len(results)} flows passed", flush=True)
        for result in results:
            if result.ok:
                print(f"PASS {result.name}")
    if walks:
        print(f"{len(walks) - len(failed_walks)}/{len(walks)} features passed", flush=True)
        for walk in walks:
            if walk.ok:
                print(f"PASS feature {walk.feature}")
    for result in failed:
        _report(result, out)
    for walk in failed_walks:
        _report_walk(walk, out)
    if cut_off:
        print(cut_off.message, file=sys.stderr)
    if failed or failed_walks or cut_off:
        return 1
    print(f"Evidence: {out}")
    return 0


def default_out() -> Path:
    return RUN_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
