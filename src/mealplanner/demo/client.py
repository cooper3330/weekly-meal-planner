"""The demo LLM client: canned answers by output model, validated, never exhausted."""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mealplanner.llm.client import Message, T, format_validation_errors, require_forbid_extra
from mealplanner.llm.errors import LLMOutputError, LLMUnavailableError

DEFAULT_LLM_DIR = Path(__file__).parent / "llm"


class DemoLLMClient:
    """Answers each call from ``<llm_dir>/<OutputModelClassName>.json``; never contacts anything."""

    def __init__(self, llm_dir: Path = DEFAULT_LLM_DIR) -> None:
        self._dir = Path(llm_dir)

    def __repr__(self) -> str:
        return f"DemoLLMClient(dir={str(self._dir)!r})"

    def generate(
        self, *, system: str, messages: list[Message], output: type[T], max_tokens: int
    ) -> T:
        require_forbid_extra(output)
        name = output.__name__
        path = self._dir / f"{name}.json"
        if not path.is_file():
            raise LLMUnavailableError(f"Demo mode has no canned answer for {name} ({path.name})")
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LLMOutputError(
                f"Canned answer {path.name} for {name} is unreadable: {exc}"
            ) from exc
        # An invalid canned file is a bug in the fixtures: fail loudly, never render it.
        try:
            return output.model_validate(payload)
        except ValidationError as exc:
            errors = format_validation_errors(exc)
            raise LLMOutputError(
                f"Canned answer {path.name} is not a valid {name}: " + "; ".join(errors), errors
            ) from exc
