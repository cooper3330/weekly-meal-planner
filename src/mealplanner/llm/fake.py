"""Scripted LLMClient for offline tests and demos. Shares the real validation/retry loop."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mealplanner.llm.client import Message, Request, Response, T, run_attempts


class FakeLLMExhaustedError(RuntimeError):
    """The fake was called more times than it has scripted responses."""


@dataclass(frozen=True)
class RecordedCall:
    system: str
    messages: list[Message]
    output: type[Any]
    max_tokens: int


class FakeLLMClient:
    def __init__(self, responses: Iterable[dict[str, Any] | BaseException]) -> None:
        self._queue: deque[dict[str, Any] | BaseException] = deque(responses)
        self.calls: list[RecordedCall] = []

    def __repr__(self) -> str:
        return f"FakeLLMClient(remaining={len(self._queue)})"

    def generate(
        self, *, system: str, messages: list[Message], output: type[T], max_tokens: int
    ) -> T:
        self.calls.append(RecordedCall(system, list(messages), output, max_tokens))
        return run_attempts(
            send=self._send,
            system=system,
            messages=messages,
            output=output,
            max_tokens=max_tokens,
        )

    def _send(self, request: Request) -> Response:
        if not self._queue:
            raise FakeLLMExhaustedError(
                "FakeLLMClient has no scripted responses left; "
                "script one more response (or fewer calls) in the test"
            )
        item = self._queue.popleft()
        if isinstance(item, BaseException):
            raise item
        return [
            {
                "type": "tool_use",
                "id": f"toolu_fake_{len(self.calls)}",
                "name": request.tool_name,
                "input": item,
            }
        ]
