"""``python -m agentbrowser up|down|browse``: the commands behind ``make app-up``, ``make app-down``
and ``make browse``."""

import argparse
import json
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from agentbrowser import lifecycle
from agentbrowser.flow import FlowError, load

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
RUN_ROOT = lifecycle.REPO_ROOT / ".agent-tmp" / "e2e"


def _app_url(name: str) -> str:
    state = lifecycle.STATE_DIR / f"{name}.json"
    try:
        url = json.loads(state.read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError, TypeError):
        raise lifecycle.LifecycleError(
            f"No running app named {name!r}. Run: make app-up APP_NAME={name}, "
            "or pass URL=http://127.0.0.1:<port>."
        ) from None
    return str(url)


def _check_loopback(url: str) -> None:
    """Only the app on this machine: the owner's identity header goes wherever the driver goes."""
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK_HOSTS:
        raise lifecycle.LifecycleError(
            f"Refusing to browse {url!r}: the base URL must be http on 127.0.0.1, ::1 or localhost."
        )


def _write_failure(out: Path, name: str, reason: str, reproduce: str) -> None:
    """The summary for a run that stopped before the browser: no browser is needed."""
    from agentbrowser.evidence import FlowResult, write_summary

    write_summary(out, [FlowResult(name, [], failures=[reason], reproduce=reproduce)])
    print(f"Summary: {out / 'summary.md'}", file=sys.stderr)


def _browse(args: argparse.Namespace) -> int:
    steps_file = Path(args.steps)
    flow_name = steps_file.stem
    reproduce = f"make browse STEPS={args.steps}"
    out = Path(args.out) if args.out else RUN_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        flow = load(steps_file)
        url = args.url or _app_url(args.name)
        _check_loopback(url)
    except (FlowError, lifecycle.LifecycleError) as exc:
        print(exc, file=sys.stderr)
        _write_failure(out, flow_name, str(exc), reproduce)
        return 1
    # Imported here so that loading a flow (and `up`/`down`) never needs Playwright.
    from agentbrowser import driver
    from agentbrowser.evidence import write_summary

    try:
        result = driver.run_flow(flow, url, out / flow_name, name=flow_name, reproduce=reproduce)
    except driver.BrowserMissingError as exc:
        print(exc, file=sys.stderr)
        return 1
    write_summary(out, [result])
    print(f"Evidence: {out}")
    if result.ok:
        return 0
    print(f"FAIL {flow_name}", file=sys.stderr)
    for reason in result.failures:
        print(f"- {reason}", file=sys.stderr)
    print(f"Summary: {out / 'summary.md'}", file=sys.stderr)
    print(f"Reproduce: {reproduce}", file=sys.stderr)
    return 1


def _e2e(args: argparse.Namespace) -> int:
    from agentbrowser import runner

    def _terminate(signum: int, frame: object) -> None:
        raise KeyboardInterrupt  # so the running app is stopped, as for Ctrl-C

    previous = signal.signal(signal.SIGTERM, _terminate)
    out = Path(args.out) if args.out else runner.default_out()
    try:
        return runner.run(
            Path(args.flow) if args.flow else None,
            out,
            map_path=Path(args.map) if args.map else None,
            feature=args.feature or None,
        )
    finally:
        signal.signal(signal.SIGTERM, previous)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbrowser")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="start the demo app")
    up.add_argument("--name", default="default")
    up.add_argument("--scenario", default="profile")
    down = sub.add_parser("down", help="stop the demo app")
    down.add_argument("--name", default="default")
    browse = sub.add_parser("browse", help="drive the app with a steps file")
    browse.add_argument("--steps", required=True)
    browse.add_argument("--url", default="")
    browse.add_argument("--out", default="")
    browse.add_argument("--name", default="default", help="app name for the state file")
    e2e = sub.add_parser(
        "e2e", help="run every flow in tests/flows/, then walk every feature in the feature map"
    )
    e2e.add_argument("--flow", default="", help="run only this flow file (no walk)")
    e2e.add_argument("--feature", default="", help="walk only this feature (no flows)")
    e2e.add_argument(
        "--map", default="", help="feature map file (default tests/e2e/feature-map.json)"
    )
    e2e.add_argument("--out", default="", help="artifact directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "e2e":
        return _e2e(args)
    try:
        if args.cmd == "up":
            handle = lifecycle.start(args.name, args.scenario)
            print(handle.url)
        elif args.cmd == "down":
            lifecycle.stop(args.name)
        else:
            return _browse(args)
    except lifecycle.LifecycleError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
