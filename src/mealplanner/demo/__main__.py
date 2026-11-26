"""``python -m mealplanner.demo``: serve the app in demo mode on loopback (ADR 0028)."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from mealplanner.config import ConfigError, load_settings
from mealplanner.demo import DemoError
from mealplanner.demo.build import LOOPBACK, build_demo_app
from mealplanner.lifeline import start_lifeline


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mealplanner.demo")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--host", default=LOOPBACK, help="a loopback address only")
    parser.add_argument(
        "--lifeline-stdin",
        action="store_true",
        help="exit when standard input reaches EOF (used by the lifecycle commands)",
    )
    args = parser.parse_args(argv)
    try:
        # Reuses the real host and port validation; the mapping is explicit, nothing is read
        # from the environment.
        settings = load_settings(
            {"MEALPLANNER_HOST": args.host, "MEALPLANNER_PORT": str(args.port)}
        )
        app = build_demo_app(args.data_dir, args.scenario, host=settings.host, port=settings.port)
    except (ConfigError, DemoError) as exc:
        parser.error(str(exc))
    if args.lifeline_stdin:
        start_lifeline()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
