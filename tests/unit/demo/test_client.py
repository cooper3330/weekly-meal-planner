import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from mealplanner.demo.client import DEFAULT_LLM_DIR, DemoLLMClient
from mealplanner.llm.errors import LLMOutputError, LLMUnavailableError


class Greeting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    count: int


class Unknown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int


def write(directory: Path, name: str, payload: object) -> None:
    (directory / f"{name}.json").write_text(json.dumps(payload))


def generate(client: DemoLLMClient, output: type[BaseModel]) -> BaseModel:
    return client.generate(
        system="s", messages=[{"role": "user", "content": "hi"}], output=output, max_tokens=10
    )


def test_returns_validated_canned_answer(tmp_path: Path) -> None:
    write(tmp_path, "Greeting", {"text": "hello", "count": 2})
    result = generate(DemoLLMClient(tmp_path), Greeting)
    assert result == Greeting(text="hello", count=2)


def test_never_runs_out(tmp_path: Path) -> None:
    write(tmp_path, "Greeting", {"text": "hello", "count": 2})
    client = DemoLLMClient(tmp_path)
    assert all(generate(client, Greeting) == Greeting(text="hello", count=2) for _ in range(100))


def test_unknown_model_is_unavailable(tmp_path: Path) -> None:
    write(tmp_path, "Greeting", {"text": "hello", "count": 2})
    with pytest.raises(LLMUnavailableError, match="Unknown"):
        generate(DemoLLMClient(tmp_path), Unknown)


@pytest.mark.parametrize(
    "payload",
    [{"text": "hello", "count": 2, "extra": 1}, {"text": "hello"}, {"text": 1, "count": 2}],
)
def test_invalid_canned_file_is_an_error(tmp_path: Path, payload: dict[str, object]) -> None:
    write(tmp_path, "Greeting", payload)
    with pytest.raises(LLMOutputError, match="Greeting"):
        generate(DemoLLMClient(tmp_path), Greeting)


def test_unreadable_json_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "Greeting.json").write_text("{not json")
    with pytest.raises(LLMOutputError, match="Greeting"):
        generate(DemoLLMClient(tmp_path), Greeting)


def test_missing_directory_means_no_answers(tmp_path: Path) -> None:
    with pytest.raises(LLMUnavailableError):
        generate(DemoLLMClient(tmp_path / "nope"), Greeting)


def test_default_directory_is_the_packaged_one() -> None:
    assert DEFAULT_LLM_DIR.is_dir()
