"""``python -m agentbrowser up|down``: the commands behind ``make app-up`` and ``make app-down``."""

import argparse
import sys
from collections.abc import Sequence

from agentbrowser import lifecycle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentbrowser")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="start the demo app")
    up.add_argument("--name", default="default")
    up.add_argument("--scenario", default="profile")
    down = sub.add_parser("down", help="stop the demo app")
    down.add_argument("--name", default="default")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "up":
            handle = lifecycle.start(args.name, args.scenario)
            print(handle.url)
        else:
            lifecycle.stop(args.name)
    except lifecycle.LifecycleError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
