"""`python -m bridge`: run the bridge on a loopback address with uvicorn."""

import argparse
import copy
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from bridge.app import create_bridge_app
from bridge.audit import configure_audit_logging
from bridge.config import BridgeConfigError, load_bridge_settings
from bridge.lifeline import start_lifeline
from bridge.registry import RegistryError, load_registry


class _DropStartupBanner(logging.Filter):
    """Drop uvicorn's "Uvicorn running on http://host:port" line: the port stays unprinted."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "running on" not in record.getMessage()


def _log_config() -> dict[str, Any]:
    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"no_banner": {"()": _DropStartupBanner}}
    config["handlers"]["default"]["filters"] = ["no_banner"]
    return config


REGISTRY_PATH = Path(__file__).resolve().parent / "commands.toml"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bridge")
    parser.add_argument(
        "--lifeline-stdin",
        action="store_true",
        help="exit when standard input reaches EOF (used by the supervisor)",
    )
    args = parser.parse_args(argv)
    if args.lifeline_stdin:
        start_lifeline()
    try:
        settings = load_bridge_settings()
        registry = load_registry(REGISTRY_PATH)
    except (BridgeConfigError, RegistryError) as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        return 2
    configure_audit_logging()
    app = create_bridge_app(settings, registry)
    # The access log records method, path and status only.
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        server_header=False,
        access_log=True,
        log_config=_log_config(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
