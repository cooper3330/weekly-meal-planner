"""``python -m mealplanner``: serve the app on the configured loopback host and port."""

import argparse
from collections.abc import Sequence

import uvicorn

from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.lifeline import start_lifeline


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mealplanner")
    parser.add_argument(
        "--lifeline-stdin",
        action="store_true",
        help="exit when standard input reaches EOF (used by the supervisor)",
    )
    args = parser.parse_args(argv)
    if args.lifeline_stdin:
        start_lifeline()
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
