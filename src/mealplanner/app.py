"""Application wiring only. Routes live in ``mealplanner.web``."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mealplanner.config import ConfigError, Settings
from mealplanner.db import apply_migrations, connect
from mealplanner.web.auth import cross_site_protection, make_identity_middleware
from mealplanner.web.routes import router

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

_STATIC_DIR = Path(__file__).parent / "web" / "static"
MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"
DB_FILENAME = "mealplanner.sqlite3"


def _write_owner_login(db_path: Path, login: str) -> None:
    """Idempotently store the owner's login on user 1 (ADR 0012)."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE users SET tailscale_login = ? WHERE id = 1", (login,))
    finally:
        conn.close()


def create_app(settings: Settings) -> FastAPI:
    db_path = settings.data_dir / DB_FILENAME

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # With only a dev login set, it becomes user 1's login so the allowlist can match it.
        owner = settings.owner_login or settings.dev_login
        if owner is None:
            raise ConfigError(
                "Set MEALPLANNER_OWNER_LOGIN to your Tailscale login "
                "(or MEALPLANNER_DEV_LOGIN for local development only)."
            )
        if settings.dev_login is not None:
            logger.warning(
                "MEALPLANNER_DEV_LOGIN is set: requests without a Tailscale-User-Login header "
                "are treated as that login. Do not set it while serving over Tailscale."
            )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(db_path, MIGRATIONS_DIR, settings.data_dir / "backups")
        _write_owner_login(db_path, owner)
        yield

    app = FastAPI(
        title="Weekly meal planner",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # The last middleware added is the outermost: security headers, then the host check,
    # then the cross-site check, then identity.
    app.middleware("http")(make_identity_middleware(db_path, settings.dev_login))
    app.middleware("http")(cross_site_protection)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", *settings.allowed_hosts],
    )

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
