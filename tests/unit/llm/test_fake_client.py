import pytest
from pydantic import BaseModel, ConfigDict

from mealplanner.llm import LLMOutputError, LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient, FakeLLMExhaustedError


class Meal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    servings: int


GOOD = {"name": "Soup", "servings": 2}
MSGS: list = [{"role": "user", "content": "hi"}]


def gen(fake: FakeLLMClient) -> Meal:
    return fake.generate(system="sys", messages=MSGS, output=Meal, max_tokens=10)


def test_returns_scripted_values_in_order() -> None:
    fake = FakeLLMClient([GOOD, {"name": "Stew", "servings": 4}])
    assert gen(fake).name == "Soup"
    assert gen(fake).name == "Stew"


def test_records_calls() -> None:
    fake = FakeLLMClient([GOOD])
    gen(fake)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.system == "sys"
    assert call.messages == MSGS
    assert call.output is Meal


def test_raises_scripted_exception() -> None:
    fake = FakeLLMClient([LLMUnavailableError("down")])
    with pytest.raises(LLMUnavailableError):
        gen(fake)


def test_invalid_then_valid_goes_through_retry_path() -> None:
    fake = FakeLLMClient([{"name": "Soup", "servings": "two"}, GOOD])
    assert gen(fake) == Meal(name="Soup", servings=2)


def test_three_invalid_raises_output_error() -> None:
    bad = {"name": "Soup", "calories": 5}
    fake = FakeLLMClient([bad, bad, bad, GOOD])
    with pytest.raises(LLMOutputError):
        gen(fake)


def test_exhausted_queue_raises_clear_error() -> None:
    fake = FakeLLMClient([])
    with pytest.raises(FakeLLMExhaustedError, match="no scripted responses"):
        gen(fake)
