"""The bridge HTTP app: access checks, then one registered command per request.

Every request passes the same checks, in order, and each failure runs nothing:
Host, Origin, bearer token, then (POST) media type and Content-Length. There is no CORS
middleware, no docs and no OpenAPI. Error bodies are `{"error": "<kind>"}` with fixed kinds;
they never carry the token, the input or any process output.
"""

import hmac
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from bridge import audit
from bridge.config import BridgeSettings
from bridge.registry import NAME_RE, Registry
from bridge.runner import RunError, RunResult, run_command

BODY_OVERHEAD_BYTES = 16384

Runner = Callable[..., RunResult]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: StrictStr = Field(pattern=NAME_RE.pattern)
    input: StrictStr


def _error(status: int, kind: str) -> JSONResponse:
    return JSONResponse({"error": kind}, status_code=status)


def _reject(status: int, kind: str) -> JSONResponse:
    audit.log_rejected(kind)
    return _error(status, kind)


def _allowed_hosts(settings: BridgeSettings) -> set[str]:
    hosts = {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}"}
    if settings.host == "::1":
        hosts.add(f"[::1]:{settings.port}")
    return hosts


def create_bridge_app(
    settings: BridgeSettings, registry: Registry, *, runner: Runner | None = None
) -> FastAPI:
    run = runner or run_command
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    allowed_hosts = _allowed_hosts(settings)
    expected_auth = f"Bearer {settings.token}".encode()
    body_cap = max(c.max_input_bytes for c in registry.commands.values()) + BODY_OVERHEAD_BYTES
    home = settings.home
    slots = threading.BoundedSemaphore(registry.max_concurrent)

    @app.middleware("http")
    async def access_checks(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host", "").lower() not in allowed_hosts:
            return _reject(403, "forbidden_host")
        if "origin" in request.headers:
            return _reject(403, "forbidden_origin")
        provided = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(provided, expected_auth):
            return _reject(401, "unauthorized")
        if request.method == "POST":
            media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if media_type != "application/json":
                return _reject(415, "unsupported_media_type")
            length = request.headers.get("content-length", "")
            if not length.isascii() or not length.isdigit():
                return _reject(422, "invalid_request")
            if int(length) > body_cap:
                return _reject(413, "input_too_large")
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes the input; this one never does.
        return _reject(422, "invalid_request")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/run", response_model=None)
    def run_endpoint(body: RunRequest) -> Any:
        def done(outcome: str, exit_status: int | None = None, duration_ms: int = 0) -> None:
            audit.log_run(
                command=body.command,
                input_text=body.input,
                outcome=outcome,
                exit_status=exit_status,
                duration_ms=duration_ms,
            )

        command = registry.commands.get(body.command)
        if command is None:
            done("unknown_command")
            return _error(404, "unknown_command")
        try:
            size = len(body.input.encode("utf-8"))
        except UnicodeEncodeError:
            # A lone surrogate has no byte length to audit, so this logs `rejected`, not `run`.
            return _reject(422, "invalid_request")
        if size > command.max_input_bytes:
            done("input_too_large")
            return _error(413, "input_too_large")
        if not slots.acquire(blocking=False):
            done("busy")
            response = _error(429, "busy")
            response.headers["Retry-After"] = "1"
            return response
        started = time.monotonic()
        try:
            result = run(command, body.input, home=home, parent_env=settings.parent_env)
        except RunError as exc:
            done(exc.kind, None, int((time.monotonic() - started) * 1000))
            return _error(exc.status, exc.kind)
        except Exception:
            # An unexpected failure is reported as a fixed kind; its text could carry content.
            done("spawn_failed", None, int((time.monotonic() - started) * 1000))
            return _error(500, "spawn_failed")
        finally:
            slots.release()
        done("exit", result.exit_code, result.duration_ms)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "duration_ms": result.duration_ms,
        }

    return app
