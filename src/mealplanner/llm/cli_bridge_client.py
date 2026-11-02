"""LLMClient over the local CLI bridge (ADR 0022).

The bridge runs the registered ``claude`` command with the rendered prompt on stdin. The JSON
Schema goes in the prompt and validation stays in code (``run_attempts``, ADR 0008).

Every error message here is a fixed string. None contains the token, the prompt, the input
messages or the CLI's output, and causes are never chained (the raise happens outside the
``except`` block, so even ``__context__`` is empty).
"""

import http.client
import json
import re
from collections.abc import Callable
from typing import Any

from mealplanner.config import Settings
from mealplanner.llm.client import Message, Request, Response, T, run_attempts
from mealplanner.llm.errors import LLMConfigError, LLMUnavailableError

COMMAND = "claude"
RUN_PATH = "/v1/run"
SOCKET_TIMEOUT_SECONDS = 150

Post = Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]

NOT_CONFIGURED = "The CLI bridge is not configured. Start the app with `make run`."
NOT_INSTALLED = "The Claude CLI is not installed at the path in src/bridge/commands.toml."
NOT_SIGNED_IN = "The Claude CLI is not signed in. Run `claude` and sign in."
_SIGN_IN = re.compile(r"(?i)(not logged in|/login|log in|sign in|authenticat|credential|oauth)")
_KIND = re.compile(r"[a-z][a-z_]{0,31}")
_FENCE = re.compile(r"\A```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?```\Z", re.DOTALL)

_HEADER = (
    "Your reply is the input to the tool `{name}` ({description}). Reply with exactly one JSON "
    "object, with no prose and no code fences, that validates against this JSON Schema:"
)


def _unavailable(kind: str) -> LLMUnavailableError:
    return LLMUnavailableError(f"The CLI bridge is unavailable ({kind}). Try again.")


class CLIBridgeLLMClient:
    """The app's only real LLMClient. ``max_tokens`` is ignored: the CLI has no per-call flag."""

    def __init__(self, settings: Settings, post: Post | None = None) -> None:
        self._settings = settings
        self._post: Post = post if post is not None else self._http_post

    def __repr__(self) -> str:
        s = self._settings
        return f"CLIBridgeLLMClient(host={s.bridge_host!r}, port={s.bridge_port!r})"

    def generate(
        self, *, system: str, messages: list[Message], output: type[T], max_tokens: int
    ) -> T:
        attempts = iter(range(1, 1000))
        return run_attempts(
            send=lambda request: self._send(request, next(attempts)),
            system=system,
            messages=messages,
            output=output,
            max_tokens=max_tokens,
        )

    # -- transport ---------------------------------------------------------------------------

    def _host_header(self) -> str:
        host = self._settings.bridge_host
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{self._settings.bridge_port}"

    def _http_post(self, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection(
            self._settings.bridge_host,
            self._settings.bridge_port,
            timeout=SOCKET_TIMEOUT_SECONDS,
        )
        try:
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    # -- one attempt -------------------------------------------------------------------------

    def _send(self, request: Request, attempt: int = 1) -> Response:
        token = self._settings.bridge_token
        if not token or self._settings.bridge_port is None:
            raise LLMConfigError(NOT_CONFIGURED)
        body = json.dumps({"command": COMMAND, "input": render_prompt(request)}).encode()
        headers = {
            "Host": self._host_header(),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        failed = False
        status, raw = 0, b""
        try:
            status, raw = self._post(RUN_PATH, body, headers)
        except TimeoutError:
            kind = "timeout"
            failed = True
        except (OSError, http.client.HTTPException):
            kind = "connection_failed"
            failed = True
        if failed:
            raise _unavailable(kind) from None
        result = self._result_text(status, raw)
        return [_to_block(result, request.tool_name, attempt)]

    def _result_text(self, status: int, raw: bytes) -> str:
        """The CLI's ``result`` string, or a mapped error. Raises outside any ``except``."""
        if status != 200:
            kind = _error_kind(raw)
            if status in (401, 403, 404) or (status == 503 and kind == "program_not_found"):
                raise LLMConfigError(NOT_INSTALLED if status == 503 else _config_message(status))
            raise _unavailable(kind if kind != "unknown" else f"http_{status}") from None
        body = _loads(raw)
        if not isinstance(body, dict):
            raise _unavailable("invalid_response") from None
        exit_code = body.get("exit_code")
        stdout = body.get("stdout")
        env = _loads(stdout.encode()) if isinstance(stdout, str) else None
        result = env.get("result") if isinstance(env, dict) else None
        failed = exit_code != 0 or (isinstance(env, dict) and env.get("is_error") is True)
        if not isinstance(exit_code, int) or not isinstance(result, str):
            raise _unavailable("invalid_envelope") from None
        if failed:
            if _SIGN_IN.search(result):
                raise LLMConfigError(NOT_SIGNED_IN)
            raise _unavailable("cli_error") from None
        return result


def _config_message(status: int) -> str:
    if status == 404:
        return "The CLI bridge does not know the claude command. Start the app with `make run`."
    return "The CLI bridge rejected the request. Start the app with `make run`."


def _loads(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _error_kind(raw: bytes) -> str:
    body = _loads(raw)
    kind = body.get("error") if isinstance(body, dict) else None
    if isinstance(kind, str) and _KIND.fullmatch(kind):
        return kind
    return "unknown"


def _to_block(result: str, tool_name: str, attempt: int) -> dict[str, Any]:
    text = result.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    obj = _loads(text.encode())
    if isinstance(obj, dict):
        return {"type": "tool_use", "id": f"cli-{attempt}", "name": tool_name, "input": obj}
    return {"type": "text", "text": result}


# -- prompt rendering -------------------------------------------------------------------------


def render_prompt(request: Request) -> str:
    parts = [
        _HEADER.format(name=request.tool_name, description=request.tool_description),
        json.dumps(request.tool_schema, sort_keys=True),
        "## Instructions",
        request.system,
        "## Conversation",
    ]
    for message in request.messages:
        parts.append(f"### {message['role']}")
        parts.append(_render_content(message["content"]))
    return "\n\n".join(parts)


def _render_content(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    out: list[str] = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            out.append(str(block.get("text", "")))
        elif kind == "tool_use":
            out.append(json.dumps(block.get("input"), sort_keys=True))
        elif kind == "tool_result":
            inner = block.get("content", "")
            out.append(inner if isinstance(inner, str) else json.dumps(inner))
    return "\n".join(out)
