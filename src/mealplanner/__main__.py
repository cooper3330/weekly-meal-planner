"""``python -m mealplanner``: serve the app on the configured loopback host and port."""

import uvicorn

from mealplanner.app import create_app
from mealplanner.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
