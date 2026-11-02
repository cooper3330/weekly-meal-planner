"""`python -m bridge`: run the bridge on a loopback address with uvicorn."""

import sys
from pathlib import Path

import uvicorn

from bridge.app import create_bridge_app
from bridge.config import BridgeConfigError, load_bridge_settings
from bridge.registry import RegistryError, load_registry

REGISTRY_PATH = Path(__file__).resolve().parent / "commands.toml"


def main() -> int:
    try:
        settings = load_bridge_settings()
        registry = load_registry(REGISTRY_PATH)
    except (BridgeConfigError, RegistryError) as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        return 2
    app = create_bridge_app(settings, registry)
    # The access log records method, path and status only.
    uvicorn.run(app, host=settings.host, port=settings.port, server_header=False, access_log=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
