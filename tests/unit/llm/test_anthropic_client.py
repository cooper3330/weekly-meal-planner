import json
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import anthropic
import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from mealplanner.app import create_app
from mealplanner.config import load_settings
from mealplanner.llm import (
    LLMClient,
    LLMConfigError,
    LLMOutputError,
    LLMUnavailableError,
)
from mealplanner.llm.anthropic_client import AnthropicLLMClient
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.web.deps import get_llm_client

FIXTURES = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "llm" / "invalid_payloads.json").read_text()
)
SENTINEL = "sk-ant-SENTINEL-do-not-leak-123"
GOOD = {"name": "Soup", "servings": 2}


class Meal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    servings: int


class LooseMeal(BaseModel):
    name: str


TOOL = "submit_Meal"


def tool_use(input: Any, name: str = TOOL, id: str = "toolu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def reply(*blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks))


class StubSDK:
    def __init__(self, *items: Any) -> None:
        self._items = list(items)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(json.loads(json.dumps(kwargs)))  # snapshot: loop mutates lists later
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make(*items: Any, key: str | None = SENTINEL) -> tuple[AnthropicLLMClient, StubSDK]:
    sdk = StubSDK(*items)
    settings = load_settings({"MEALPLANNER_MODEL": "test-model"})
    return AnthropicLLMClient(settings, sdk_client=sdk), sdk


def gen(client: LLMClient) -> Meal:
    return client.generate(
        system="be helpful",
        messages=[{"role": "user", "content": "hi"}],
        output=Meal,
        max_tokens=99,
    )


def test_valid_first_response() -> None:
    client, sdk = make(reply(tool_use(GOOD)))
    assert gen(client) == Meal(name="Soup", servings=2)
    assert len(sdk.calls) == 1
    call = sdk.calls[0]
    assert call["model"] == "test-model"
    assert call["max_tokens"] == 99
    assert call["tools"][0]["name"] == TOOL
    assert call["tools"][0]["input_schema"] == Meal.model_json_schema()
    assert call["tool_choice"] == {"type": "tool", "name": TOOL}
    assert call["system"][0]["text"] == "be helpful"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_invalid_then_valid() -> None:
    client, sdk = make(
        reply(tool_use(FIXTURES["wrong_type"], id="toolu_bad")), reply(tool_use(GOOD))
    )
    assert gen(client) == Meal(name="Soup", servings=2)
    assert len(sdk.calls) == 2
    msgs = sdk.calls[1]["messages"]
    assert msgs[0] == {"role": "user", "content": "hi"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[1]["content"][0]["id"] == "toolu_bad"
    result = msgs[2]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "toolu_bad"
    assert result["is_error"] is True
    assert "servings" in result["content"]


def test_three_invalid_raises() -> None:
    bad = reply(tool_use(FIXTURES["wrong_type"]))
    client, sdk = make(bad, bad, bad, reply(tool_use(GOOD)))
    with pytest.raises(LLMOutputError) as info:
        gen(client)
    assert len(sdk.calls) == 3
    assert any("servings" in e for e in info.value.errors)


def test_text_only_response_is_invalid() -> None:
    text = reply(SimpleNamespace(type="text", text="Here is a soup."))
    client, sdk = make(text, reply(tool_use(GOOD)))
    assert gen(client) == Meal(name="Soup", servings=2)
    assert len(sdk.calls) == 2


def test_wrong_tool_name_is_invalid() -> None:
    client, sdk = make(reply(tool_use(GOOD, name="other")), reply(tool_use(GOOD)))
    assert gen(client) == Meal(name="Soup", servings=2)
    assert len(sdk.calls) == 2
    assert sdk.calls[1]["messages"][2]["content"][0]["is_error"] is True


def test_extra_field_rejected() -> None:
    bad = reply(tool_use(FIXTURES["extra_calories"]))
    client, sdk = make(bad, bad, bad)
    with pytest.raises(LLMOutputError) as info:
        gen(client)
    assert any("calories" in e for e in info.value.errors)


def test_model_without_forbid_rejected() -> None:
    client, sdk = make(reply(tool_use({"name": "x"})))
    with pytest.raises(TypeError):
        client.generate(system="s", messages=[], output=LooseMeal, max_tokens=10)
    assert sdk.calls == []


@pytest.mark.parametrize("name", ["wrong_type", "missing_field", "not_an_object"])
def test_wrong_types_and_missing_fields_invalid(name: str) -> None:
    bad = reply(tool_use(FIXTURES[name]))
    client, sdk = make(bad, bad, bad)
    with pytest.raises(LLMOutputError):
        gen(client)
    assert len(sdk.calls) == 3


def _http_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


@pytest.mark.parametrize(
    "exc",
    [
        anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        _http_error(anthropic.RateLimitError, 429),
        _http_error(anthropic.InternalServerError, 500),
    ],
)
def test_transport_errors_map_to_unavailable(exc: Exception) -> None:
    client, sdk = make(exc, reply(tool_use(GOOD)))
    with pytest.raises(LLMUnavailableError):
        gen(client)
    assert len(sdk.calls) == 1


def test_rejected_key_maps_to_config_error() -> None:
    client, sdk = make(_http_error(anthropic.AuthenticationError, 401), reply(tool_use(GOOD)))
    with pytest.raises(LLMConfigError) as info:
        gen(client)
    assert len(sdk.calls) == 1
    assert SENTINEL not in str(info.value)


def test_other_client_errors_are_not_mapped() -> None:
    client, sdk = make(_http_error(anthropic.BadRequestError, 400), reply(tool_use(GOOD)))
    with pytest.raises(anthropic.BadRequestError):
        gen(client)
    assert len(sdk.calls) == 1


def test_missing_key_raises_config_error_on_call() -> None:
    client = AnthropicLLMClient(load_settings({}))
    with pytest.raises(LLMConfigError) as info:
        gen(client)
    assert "ANTHROPIC_API_KEY" in str(info.value)


def test_app_starts_without_key() -> None:
    app = create_app(load_settings({}))
    assert TestClient(app).get("/healthz").status_code == 200


def test_sdk_client_created_lazily_with_two_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []
    stub = StubSDK(reply(tool_use(GOOD)))

    def fake_anthropic(**kwargs: Any) -> StubSDK:
        created.append(kwargs)
        return stub

    monkeypatch.setattr(anthropic, "Anthropic", fake_anthropic)
    client = AnthropicLLMClient(load_settings({"ANTHROPIC_API_KEY": SENTINEL}))
    assert created == []
    gen(client)
    assert created == [{"api_key": SENTINEL, "max_retries": 2}]


def test_key_not_leaked() -> None:
    settings = load_settings({"ANTHROPIC_API_KEY": SENTINEL})
    assert SENTINEL not in repr(settings)
    assert SENTINEL not in str(settings)
    sdk = StubSDK(
        _http_error(anthropic.RateLimitError, 429),
        reply(tool_use(FIXTURES["wrong_type"])),
        reply(tool_use(FIXTURES["wrong_type"])),
        reply(tool_use(FIXTURES["wrong_type"])),
    )
    client = AnthropicLLMClient(settings, sdk_client=sdk)
    assert SENTINEL not in repr(client)
    assert SENTINEL not in str(client)
    for _ in range(2):
        with pytest.raises((LLMUnavailableError, LLMOutputError)) as info:
            gen(client)
        assert SENTINEL not in str(info.value)
        assert SENTINEL not in repr(info.value)


def test_app_uses_injected_client() -> None:
    fake = FakeLLMClient([GOOD])
    app: FastAPI = create_app(load_settings({}), llm_client=fake)

    @app.get("/_probe")
    def probe(client: Annotated[LLMClient, Depends(get_llm_client)]) -> dict[str, bool]:
        return {"same": client is fake}

    assert TestClient(app).get("/_probe").json() == {"same": True}
