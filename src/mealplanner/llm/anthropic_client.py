"""Anthropic implementation of LLMClient (forced tool use, ADR 0008)."""

from typing import Any

import anthropic

from mealplanner.config import Settings
from mealplanner.llm.client import Message, Request, Response, T, run_attempts
from mealplanner.llm.errors import LLMConfigError, LLMUnavailableError

SDK_MAX_RETRIES = 2


class AnthropicLLMClient:
    def __init__(self, settings: Settings, sdk_client: Any | None = None) -> None:
        self._settings = settings
        self._sdk = sdk_client

    def __repr__(self) -> str:
        return f"AnthropicLLMClient(model={self._settings.model!r})"

    def _sdk_client(self) -> Any:
        if self._sdk is None:
            key = self._settings.anthropic_api_key
            if not key:
                raise LLMConfigError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env and run with "
                    "`uv run --env-file .env`."
                )
            self._sdk = anthropic.Anthropic(api_key=key, max_retries=SDK_MAX_RETRIES)
        return self._sdk

    def generate(
        self, *, system: str, messages: list[Message], output: type[T], max_tokens: int
    ) -> T:
        return run_attempts(
            send=self._send,
            system=system,
            messages=messages,
            output=output,
            max_tokens=max_tokens,
        )

    def _send(self, request: Request) -> Response:
        sdk = self._sdk_client()
        try:
            raw = sdk.messages.create(
                model=self._settings.model,
                max_tokens=request.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=request.messages,
                tools=[
                    {
                        "name": request.tool_name,
                        "description": request.tool_description,
                        "input_schema": request.tool_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": request.tool_name},
            )
        except anthropic.AuthenticationError as exc:
            raise LLMConfigError("The Claude API rejected the configured API key.") from exc
        except (anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
            raise LLMUnavailableError(f"Claude API unavailable ({type(exc).__name__}).") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMUnavailableError(
                    f"Claude API unavailable (HTTP {exc.status_code})."
                ) from exc
            raise
        return [_block_to_dict(b) for b in raw.content]


def _block_to_dict(block: Any) -> dict[str, Any]:
    kind = getattr(block, "type", None)
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if kind == "text":
        return {"type": "text", "text": block.text}
    return {"type": str(kind)}
