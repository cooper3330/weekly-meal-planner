from typing import Any

import pytest

from mealplanner.llm.client import Request, Response
from mealplanner.llm.errors import LLMConfigError, LLMOutputError, LLMUnavailableError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.recipes.errors import RecipeGenerationError
from mealplanner.recipes.generate import generate_recipe_steps
from mealplanner.recipes.models import RecipeSteps
from recipes.recipe_support import CAT, make_meal, payload_with_text, valid_payload

MEAL = make_meal()
PEANUTS = frozenset({ExclusionTerm.peanuts})


class RecordingFake(FakeLLMClient):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.requests: list[Request] = []

    def _send(self, request: Request) -> Response:
        self.requests.append(request)
        return super()._send(request)


def good() -> dict[str, Any]:
    return valid_payload(MEAL)


def peanut_text() -> dict[str, Any]:
    return payload_with_text(MEAL, "Top with crushed peanuts.")


def run(fake: FakeLLMClient) -> RecipeSteps:
    return generate_recipe_steps(fake, MEAL, catalog=CAT, exclusions=PEANUTS)


def test_generate_happy_path() -> None:
    fake = FakeLLMClient([good()])
    steps = run(fake)
    assert isinstance(steps, RecipeSteps)
    assert len(fake.calls) == 1
    assert steps.prep_minutes == 10
    assert steps.steps[0].text == "Get everything ready."


def test_rejected_then_replaced() -> None:
    fake = FakeLLMClient([peanut_text(), good()])
    steps = run(fake)
    assert len(fake.calls) == 2
    assert steps.steps[1].text == "Cook it all together, then serve."
    # The replacement request carries the reason.
    assert "peanuts" in fake.calls[1].messages[0]["content"]
    assert "(none)" in fake.calls[0].messages[0]["content"]


def test_replacement_carries_all_reasons_so_far() -> None:
    soy = payload_with_text(MEAL, "Splash in some soy sauce.")
    fake = FakeLLMClient([peanut_text(), soy, good()])
    fake_exclusions = frozenset({ExclusionTerm.peanuts, ExclusionTerm.soy})
    generate_recipe_steps(fake, MEAL, catalog=CAT, exclusions=fake_exclusions)
    third = str(fake.calls[2].messages[0]["content"])
    assert "'peanuts'" in third
    assert "'soy sauce'" in third


def test_rejected_three_times() -> None:
    fake = FakeLLMClient([peanut_text(), peanut_text(), peanut_text()])
    with pytest.raises(RecipeGenerationError) as err:
        run(fake)
    assert len(fake.calls) == 3
    assert len(err.value.reasons) == 3
    assert err.value.meal_name == MEAL.draft.name


def test_no_fourth_call_is_made() -> None:
    # A fourth scripted response would be unused; the fake would raise if one were wanted
    # beyond the script, so script exactly three and one spare.
    fake = FakeLLMClient([peanut_text()] * 3 + [good()])
    with pytest.raises(RecipeGenerationError):
        run(fake)
    assert len(fake.calls) == 3


@pytest.mark.parametrize(
    "error",
    [LLMUnavailableError("down"), LLMConfigError("no key"), LLMOutputError("bad", ["x"])],
)
def test_llm_errors_pass_through(error: Exception) -> None:
    with pytest.raises(type(error)):
        run(FakeLLMClient([error]))


def test_schema_failures_use_the_clients_retry_not_a_replacement_call() -> None:
    bad = payload_with_text(MEAL, "Add 200 g chicken")
    fake = RecordingFake([bad, good()])
    run(fake)
    assert len(fake.calls) == 1  # one generate call
    assert len(fake.requests) == 2  # two attempts inside it


def test_every_call_uses_the_per_meal_schema() -> None:
    fake = FakeLLMClient([good()])
    run(fake)
    assert fake.calls[0].output.__name__ == "RecipeDraft"
    assert "enum" in str(fake.calls[0].output.model_json_schema())


def test_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # The autouse guard blocks the network, and this removes any key. The bridge is not
    # running either: only the fake is used.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(run(FakeLLMClient([good()])), RecipeSteps)
