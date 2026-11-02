import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.llm import LLMConfigError, LLMOutputError, LLMUnavailableError
from mealplanner.llm.cli_bridge_client import CLIBridgeLLMClient

TOKEN = "TOKEN-SENTINEL-abc123"
SYSTEM = "SYSTEM-SENTINEL-plan meals"
USER = "USER-SENTINEL-i like soup"
STDOUT_SENTINEL = "STDOUT-SENTINEL-secret-output"
SENTINELS = (TOKEN, SYSTEM, USER, STDOUT_SENTINEL)
GOOD = {"name": "Soup", "servings": 2}


class Meal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    servings: int


Reply = tuple[int, bytes]


def envelope(result: str, *, is_error: bool = False) -> str:
    return json.dumps({"type": "result", "is_error": is_error, "result": result})


def ok(result: str, *, exit_code: int = 0, is_error: bool = False) -> Reply:
    return ok_stdout(envelope(result, is_error=is_error), exit_code)


def ok_stdout(stdout: str, exit_code: int = 0) -> Reply:
    body = {"exit_code": exit_code, "stdout": stdout, "duration_ms": 5}
    return 200, json.dumps(body).encode()


def err(status: int, kind: str = "kind") -> Reply:
    return status, json.dumps({"error": kind}).encode()


class FakePost:
    def __init__(self, *items: Reply | BaseException) -> None:
        self.items = list(items)
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, path: str, body: bytes, headers: dict[str, str]) -> Reply:
        self.calls.append((path, json.loads(body), dict(headers)))
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make(
    *items: Reply | BaseException, token: str | None = TOKEN, port: str | None = "9999"
) -> tuple[CLIBridgeLLMClient, FakePost]:
    env: dict[str, str] = {}
    if token is not None:
        env["MEALPLANNER_BRIDGE_TOKEN"] = token
    if port is not None:
        env["MEALPLANNER_BRIDGE_PORT"] = port
    post = FakePost(*items)
    return CLIBridgeLLMClient(load_settings(env), post=post), post


def gen(client: CLIBridgeLLMClient) -> Meal:
    return client.generate(
        system=SYSTEM,
        messages=[{"role": "user", "content": USER}],
        output=Meal,
        max_tokens=100,
    )


def test_valid_first_reply() -> None:
    client, post = make(ok(json.dumps(GOOD)))
    assert gen(client) == Meal(**GOOD)
    assert len(post.calls) == 1
    path, body, headers = post.calls[0]
    assert path == "/v1/run"
    assert body["command"] == "claude"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Content-Type"] == "application/json"
    assert headers["Host"] == "127.0.0.1:9999"
    rendered = body["input"]
    assert SYSTEM in rendered
    assert "submit_Meal" in rendered
    assert json.dumps(Meal.model_json_schema(), sort_keys=True) in rendered
    assert USER in rendered


def test_fenced_json_accepted() -> None:
    client, post = make(ok("```json\n" + json.dumps(GOOD) + "\n```\n"))
    assert gen(client) == Meal(**GOOD)
    assert len(post.calls) == 1


def test_prose_before_json_is_retried() -> None:
    client, post = make(ok("Here you go: " + json.dumps(GOOD)), ok(json.dumps(GOOD)))
    assert gen(client) == Meal(**GOOD)
    assert len(post.calls) == 2


def test_invalid_twice_then_valid() -> None:
    first = "FIRST-REPLY-not json at all"
    second = json.dumps({"name": "Soup", "servings": "many"})
    client, post = make(ok(first), ok(second), ok(json.dumps(GOOD)))
    assert gen(client) == Meal(**GOOD)
    assert len(post.calls) == 3
    third = post.calls[2][1]["input"]
    assert "servings" in third
    assert first in third


def test_three_invalid_raises_output_error() -> None:
    client, post = make(ok("nope"), ok("nope"), ok("[1, 2]"))
    with pytest.raises(LLMOutputError):
        gen(client)
    assert len(post.calls) == 3


def test_extra_field_rejected() -> None:
    bad = json.dumps(dict(GOOD, calories=100))
    client, post = make(ok(bad), ok(bad), ok(bad))
    with pytest.raises(LLMOutputError):
        gen(client)
    assert len(post.calls) == 3


def test_tool_use_and_error_rendered_in_conversation() -> None:
    client, post = make(ok(json.dumps({"name": "Soup"})), ok(json.dumps(GOOD)))
    gen(client)
    second = post.calls[1][1]["input"]
    assert '"name": "Soup"' in second
    assert "Invalid output" in second


SIGN_IN = "Not logged in · Please run /login"

CASES: list[tuple[str, Any, type[Exception]]] = [
    ("refused", ConnectionRefusedError("x"), LLMUnavailableError),
    ("timeout", TimeoutError("x"), LLMUnavailableError),
    ("reset", ConnectionResetError("x"), LLMUnavailableError),
    *[(f"status{s}", err(s), LLMConfigError) for s in (401, 403, 404)],
    *[
        (f"status{s}", err(s), LLMUnavailableError)
        for s in (413, 415, 422, 429, 500, 502, 504, 418)
    ],
    ("503-notfound", err(503, "program_not_found"), LLMConfigError),
    ("503-other", err(503, "other"), LLMUnavailableError),
    ("exit1", ok("boom", exit_code=1), LLMUnavailableError),
    ("exit1-signin", ok(SIGN_IN, exit_code=1), LLMConfigError),
    ("is_error", ok("boom", is_error=True), LLMUnavailableError),
    ("is_error-signin", ok(SIGN_IN, is_error=True), LLMConfigError),
    ("garbage", ok_stdout("garbage"), LLMUnavailableError),
    ("no-result", ok_stdout("{}"), LLMUnavailableError),
    ("non-json-body", (200, b"<html>"), LLMUnavailableError),
]


@pytest.mark.parametrize(("label", "item", "exc_type"), CASES, ids=[c[0] for c in CASES])
def test_error_mapping(label: str, item: Any, exc_type: type[Exception]) -> None:
    client, post = make(item)
    with pytest.raises(exc_type):
        gen(client)
    assert len(post.calls) == 1


def test_unavailable_message_is_fixed() -> None:
    client, _ = make(err(502, "output_too_large"))
    with pytest.raises(LLMUnavailableError) as info:
        gen(client)
    assert str(info.value) == "The CLI bridge is unavailable (output_too_large). Try again."


def test_hostile_error_code_not_echoed() -> None:
    client, _ = make(err(502, "evil " + STDOUT_SENTINEL))
    with pytest.raises(LLMUnavailableError) as info:
        gen(client)
    assert STDOUT_SENTINEL not in str(info.value)


@pytest.mark.parametrize(("token", "port"), [(None, "9999"), (TOKEN, None), (None, None)])
def test_not_configured_is_config_error(token: str | None, port: str | None) -> None:
    client, post = make(ok(json.dumps(GOOD)), token=token, port=port)
    with pytest.raises(LLMConfigError) as info:
        gen(client)
    assert "make run" in str(info.value)
    assert post.calls == []


def _leak_items() -> list[Any]:
    leaky = STDOUT_SENTINEL
    return [
        ConnectionRefusedError(f"{TOKEN} {USER}"),
        TimeoutError(leaky),
        (401, leaky.encode()),
        err(502, leaky),
        (503, json.dumps({"error": "program_not_found", "x": leaky}).encode()),
        ok(leaky, exit_code=1),
        ok(f"{SIGN_IN} {leaky}", exit_code=1),
        ok_stdout(leaky),
        (200, leaky.encode()),
    ]


@pytest.mark.parametrize("index", range(9))
def test_errors_leak_nothing(index: int, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client, _ = make(_leak_items()[index])
    with pytest.raises((LLMConfigError, LLMUnavailableError)) as info:
        gen(client)
    _assert_clean(info.value, client, caplog)


def test_output_error_leaks_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    bad = f"{STDOUT_SENTINEL} not json"
    client, _ = make(ok(bad), ok(bad), ok(bad))
    with pytest.raises(LLMOutputError) as info:
        gen(client)
    _assert_clean(info.value, client, caplog)


def _assert_clean(exc: BaseException, client: object, caplog: pytest.LogCaptureFixture) -> None:
    texts = [str(exc), repr(exc), repr(client), caplog.text]
    for linked in (exc.__cause__, exc.__context__):
        if linked is not None:
            texts += [str(linked), repr(linked)]
    for text in texts:
        for sentinel in SENTINELS:
            assert sentinel not in text


def _app_env(tmp_path: Path) -> dict[str, str]:
    return {"MEALPLANNER_DATA_DIR": str(tmp_path), "MEALPLANNER_OWNER_LOGIN": "owner@example.com"}


def test_default_client_is_bridge_client(tmp_path: Path) -> None:
    app = create_app(load_settings(_app_env(tmp_path)))
    assert isinstance(app.state.llm_client, CLIBridgeLLMClient)
    with_key = dict(_app_env(tmp_path), ANTHROPIC_API_KEY="k")
    assert isinstance(create_app(load_settings(with_key)).state.llm_client, CLIBridgeLLMClient)


def test_building_client_never_contacts_bridge() -> None:
    post = FakePost()
    CLIBridgeLLMClient(load_settings({}), post=post)
    assert post.calls == []


def test_bridge_down_app_starts(tmp_path: Path) -> None:
    env = dict(_app_env(tmp_path), MEALPLANNER_BRIDGE_PORT="9999", MEALPLANNER_BRIDGE_TOKEN=TOKEN)
    settings = load_settings(env)
    app = create_app(settings)
    client = CLIBridgeLLMClient(settings, post=FakePost(ConnectionRefusedError("down")))
    app.state.llm_client = client
    headers = {"Tailscale-User-Login": "owner@example.com"}
    with TestClient(app, base_url="http://localhost", headers=headers) as c:
        assert c.get("/").status_code == 200
        with pytest.raises(LLMUnavailableError) as info:
            gen(client)
    assert str(info.value) == "The CLI bridge is unavailable (connection_failed). Try again."
