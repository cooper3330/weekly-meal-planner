"""Application wiring only. Routes live in ``mealplanner.web``."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from mealplanner.config import Settings
from mealplanner.llm.anthropic_client import AnthropicLLMClient
from mealplanner.llm.client import LLMClient
from mealplanner.db import apply_migrations
from mealplanner.web.routes import router

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

_STATIC_DIR = Path(__file__).parent / "web" / "static"
MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"
DB_FILENAME = "mealplanner.sqlite3"


def create_app(settings: Settings, llm_client: LLMClient | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(
            settings.data_dir / DB_FILENAME, MIGRATIONS_DIR, settings.data_dir / "backups"
        )
        yield

    app = FastAPI(
        title="Weekly meal planner",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Building the client never needs the API key; a missing key surfaces on generate().
    app.state.llm_client = llm_client if llm_client is not None else AnthropicLLMClient(settings)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.include_router(router)
    return app
