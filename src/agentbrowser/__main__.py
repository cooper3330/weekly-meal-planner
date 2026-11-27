"""``python -m agentbrowser up|down|browse``: the commands behind ``make app-up``, ``make app-down``
and ``make browse``."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentbrowser import lifecycle
from agentbrowser.flow import FlowError, load

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


def _browse(args: argparse.Namespace) -> int:
    steps_file = Path(args.steps)
    try:
        flow = load(steps_file)
    except FlowError as exc:
        print(exc, file=sys.stderr)
        return 1
    url = args.url or _app_url(args.name)
    out = Path(args.out) if args.out else RUN_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    # Imported here so that loading a flow (and `up`/`down`) never needs Playwright.
    from agentbrowser import driver
    from agentbrowser.evidence import write_summary

    flow_name = steps_file.stem
    reproduce = f"make browse STEPS={args.steps}"
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


def main(argv: Sequence[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
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
