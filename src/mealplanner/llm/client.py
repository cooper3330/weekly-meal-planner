"""The LLM boundary: protocol, message type, and the one shared attempt loop."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, TypeVar

from pydantic import BaseModel, ValidationError

from mealplanner.llm.errors import LLMOutputError

T = TypeVar("T", bound=BaseModel)

MAX_ATTEMPTS = 3


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


class LLMClient(Protocol):
    def generate(
        self, *, system: str, messages: list[Message], output: type[T], max_tokens: int
    ) -> T: ...


@dataclass(frozen=True)
class Request:
    """One model call, independent of the SDK."""

    system: str
    messages: list[Message]
    tool_name: str
    tool_description: str
    tool_schema: dict[str, Any]
    max_tokens: int


# A response is the list of content blocks, as plain dicts:
# {"type": "tool_use", "id": ..., "name": ..., "input": ...} or {"type": "text", "text": ...}.
Response = list[dict[str, Any]]
Send = Callable[[Request], Response]


def tool_name_for(output: type[BaseModel]) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]", "_", output.__name__)
    return f"submit_{name}"[:64]


def require_forbid_extra(output: type[BaseModel]) -> None:
    if output.model_config.get("extra") != "forbid":
        raise TypeError(
            f"{output.__name__} must set model_config = ConfigDict(extra='forbid') "
            "so model-supplied extra fields (such as nutrition numbers) are rejected"
        )


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Field path and message only; never the offending input values."""
    out: list[str] = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        path = ".".join(str(p) for p in err["loc"]) or "(root)"
        out.append(f"{path}: {err['msg']}")
    return out


def run_attempts(
    *,
    send: Send,
    system: str,
    messages: list[Message],
    output: type[T],
    max_tokens: int,
    max_attempts: int = MAX_ATTEMPTS,
) -> T:
    """Call ``send`` until the output validates, at most ``max_attempts`` times in total.

    Transport errors raised by ``send`` propagate immediately (not retried here).
    Never returns partial or invalid output.
    """
    require_forbid_extra(output)
    tool_name = tool_name_for(output)
    description = (output.__doc__ or f"Return the {output.__name__} result.").strip()
    schema = output.model_json_schema()
    convo: list[Message] = list(messages)
    last_errors: list[str] = []

    for _ in range(max_attempts):
        response = send(
            Request(
                system=system,
                messages=list(convo),
                tool_name=tool_name,
                tool_description=description,
                tool_schema=schema,
                max_tokens=max_tokens,
            )
        )
        tool_use = next((b for b in response if b.get("type") == "tool_use"), None)
        if tool_use is None:
            last_errors = [f"no {tool_name} tool call in the response"]
            convo.append({"role": "assistant", "content": _text_of(response)})
            convo.append(
                {
                    "role": "user",
                    "content": f"Invalid: call the {tool_name} tool with the result.",
                }
            )
            continue
        block: dict[str, Any] = {
            "type": "tool_use",
            "id": tool_use.get("id", ""),
            "name": tool_use.get("name", ""),
            "input": tool_use.get("input"),
        }
        if block["name"] != tool_name:
            last_errors = [f"wrong tool {block['name']!r}; call {tool_name!r}"]
        else:
            try:
                return output.model_validate(block["input"])
            except ValidationError as exc:
                last_errors = format_validation_errors(exc)
        convo.append({"role": "assistant", "content": [block]})
        convo.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "is_error": True,
                        "content": "Invalid output:\n" + "\n".join(last_errors),
                    }
                ],
            }
        )

    raise LLMOutputError(
        f"No valid {output.__name__} after {max_attempts} attempts: " + "; ".join(last_errors),
        last_errors,
    )


def _text_of(response: Response) -> str:
    text = " ".join(str(b.get("text", "")) for b in response if b.get("type") == "text").strip()
    return text or "(no content)"
