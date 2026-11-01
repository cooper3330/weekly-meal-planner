"""Access control (ADR 0012): Tailscale identity allowlist and cross-site request checks.

The identity middleware is the only place a user id is produced; routes read it through
``current_user_id``.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from mealplanner.config import normalize_login
from mealplanner.db import connect

LOGIN_HEADER = "Tailscale-User-Login"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
STATIC_PREFIX = "/static/"

Next = Callable[[Request], Awaitable[Response]]


def forbidden() -> Response:
    """A plain 403 page that carries no user data."""
    return PlainTextResponse("Forbidden", status_code=403)


def _lookup_user_id(db_path: Path, login: str) -> int | None:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT id FROM users WHERE tailscale_login = ?", (login,)).fetchone()
    finally:
        conn.close()
    return int(row["id"]) if row is not None else None


def make_identity_middleware(
    db_path: Path, dev_login: str | None
) -> Callable[..., Awaitable[Response]]:
    async def identity(request: Request, call_next: Next) -> Response:
        if request.url.path.startswith(STATIC_PREFIX):
            return await call_next(request)
        raw = request.headers.get(LOGIN_HEADER)
        # The dev login applies only when the header is absent; a present header always wins.
        login = normalize_login(raw) if raw is not None else dev_login
        user_id = _lookup_user_id(db_path, login) if login else None
        if user_id is None:
            return forbidden()
        request.state.user_id = user_id
        return await call_next(request)

    return identity


def _origin_matches(request: Request, origin: str) -> bool:
    parts = urlsplit(origin)
    host = request.headers.get("host", "")
    if not parts.netloc or parts.netloc.lower() != host.lower():
        return False
    return parts.scheme == request.url.scheme


async def cross_site_protection(request: Request, call_next: Next) -> Response:
    if request.method in UNSAFE_METHODS:
        site = request.headers.get("sec-fetch-site")
        if site is not None:
            allowed = site in ("same-origin", "none")
        else:
            origin = request.headers.get("origin")
            allowed = origin is not None and _origin_matches(request, origin)
        if not allowed:
            return forbidden()
    return await call_next(request)


def current_user_id(request: Request) -> int:
    """FastAPI dependency: the only way routes get the user id."""
    user_id = getattr(request.state, "user_id", None)
    if not isinstance(user_id, int):
        raise HTTPException(status_code=403, detail="Forbidden")
    return user_id
