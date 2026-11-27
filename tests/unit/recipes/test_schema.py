from typing import Any

import pytest
from pydantic import ValidationError

from mealplanner.llm.client import Request, Response
from mealplanner.llm.errors import LLMOutputError
from mealplanner.llm.fake import FakeLLMClient
from mealplanner.recipes.schema import RecipeDraft, recipe_draft_model
from recipes.recipe_support import (
    CAT,
    make_meal,
    meal_ids,
    payload_with_text,
    valid_payload,
)

MEAL = make_meal()
EGG_ONION = make_meal(("egg", "onion"), name="Egg and onion scramble")


def test_valid_draft_accepted() -> None:
    draft = recipe_draft_model(MEAL, catalog=CAT).model_validate(valid_payload(MEAL))
    assert isinstance(draft, RecipeDraft)
    assert draft.prep_minutes == 10
    assert len(draft.steps) == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"title": "Mine"},
        {"servings": 4},
        {"ingredients": []},
        {"calories": 500},
        {"protein_g": 30},
        {"day": "monday"},
    ],
)
def test_extra_fields_rejected(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        recipe_draft_model(MEAL, catalog=CAT).model_validate({**valid_payload(MEAL), **extra})


@pytest.mark.parametrize("extra", [{"quantity": 2}, {"kcal": 100}])
def test_step_extra_fields_rejected(extra: dict[str, Any]) -> None:
    payload = valid_payload(MEAL)
    payload["steps"][0].update(extra)
    with pytest.raises(ValidationError):
        recipe_draft_model(MEAL, catalog=CAT).model_validate(payload)


def test_step_id_not_in_meal() -> None:
    # "milk" is a catalog id that this meal does not use; "mystery" is not in the catalog.
    for bad in ("milk", "mystery"):
        payload = payload_with_text(MEAL, "Stir it.", ids=[bad])
        with pytest.raises(ValidationError) as err:
            recipe_draft_model(MEAL, catalog=CAT).model_validate(payload)
        assert repr(bad) in str(err.value)
        assert "not in this meal" in str(err.value)


def test_long_step_id_is_cut_to_64_characters() -> None:
    long_id = "x" * 200
    payload = payload_with_text(MEAL, "Stir it.", ids=[long_id])
    with pytest.raises(ValidationError) as err:
        recipe_draft_model(MEAL, catalog=CAT).model_validate(payload)
    assert repr(long_id)[:64] in str(err.value)
    assert repr(long_id)[:65] not in str(err.value)


def test_non_string_step_id_fails_cleanly() -> None:
    payload = payload_with_text(MEAL, "Stir it.", ids=[7])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        recipe_draft_model(MEAL, catalog=CAT).model_validate(payload)


def test_every_ingredient_used() -> None:
    payload = valid_payload(MEAL)
    payload["steps"][0]["ingredient_ids"] = [i for i in meal_ids(MEAL) if i != "rice"]
    with pytest.raises(ValidationError) as err:
        recipe_draft_model(MEAL, catalog=CAT).model_validate(payload)
    assert "'rice' is never used in a step" in str(err.value)


def test_prep_bounds() -> None:
    model = recipe_draft_model(MEAL, catalog=CAT)
    cook = MEAL.draft.cook_minutes
    for ok in (0, cook):
        assert model.model_validate(valid_payload(MEAL, prep=ok)).prep_minutes == ok
    for bad in (-1, cook + 1, True, 2.5):
        with pytest.raises(ValidationError):
            model.model_validate(valid_payload(MEAL, prep=bad))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "Add 200 g chicken",
        "add 2 cups rice",
        "1/2 tsp salt",
        "1 1/2 tbsp oil",
        "½ cup stock",
        "0.5 kg potatoes",
        "3 Tablespoons butter",
        "8 oz pasta",
        "1 lb beef",
        "250ml milk",
        "1 l stock",
    ],
)
def test_amounts_in_text_rejected(text: str) -> None:
    with pytest.raises(ValidationError) as err:
        recipe_draft_model(MEAL, catalog=CAT).model_validate(payload_with_text(MEAL, text))
    assert "step 2 states an amount" in str(err.value)


@pytest.mark.parametrize("text", ["Crack 3 eggs", "Dice 2 onions", "peel 1 Onion"])
def test_counts_of_own_ingredients_rejected(text: str) -> None:
    with pytest.raises(ValidationError) as err:
        recipe_draft_model(EGG_ONION, catalog=CAT).model_validate(
            payload_with_text(EGG_ONION, text)
        )
    assert "states an amount" in str(err.value)


@pytest.mark.parametrize(
    "text",
    [
        "Bake at 200 °C for 20 minutes",
        "Heat the oven to 400 F",
        "Simmer 5-7 min",
        "Cut into 2 cm pieces",
        "Serve 2 per plate",
        "Cut the onion into 8 wedges",
    ],
)
def test_times_and_temperatures_allowed(text: str) -> None:
    model = recipe_draft_model(EGG_ONION, catalog=CAT)
    assert model.model_validate(payload_with_text(EGG_ONION, text))


@pytest.mark.parametrize(
    "text",
    [
        "Use 2 large pans",
        "Squeeze 2 limes over the top",
        "Grate 2 ginger knobs",
        "Fold in 3 layers",
        "Serve 4 guests",
    ],
)
def test_unit_words_whole_word_only(text: str) -> None:
    assert recipe_draft_model(MEAL, catalog=CAT).model_validate(payload_with_text(MEAL, text))


def test_count_rule_only_uses_this_meals_ingredients() -> None:
    # "3 eggs" is only an amount when the meal has eggs.
    assert recipe_draft_model(MEAL, catalog=CAT).model_validate(
        payload_with_text(MEAL, "Serve 3 eggs")
    )


def test_lengths() -> None:
    model = recipe_draft_model(MEAL, catalog=CAT)
    ok = valid_payload(MEAL)
    step = {"text": "Stir it well.", "ingredient_ids": []}
    for steps in ([], [ok["steps"][0]] + [step] * 20):
        with pytest.raises(ValidationError):
            model.model_validate({"prep_minutes": 5, "steps": steps})
    assert model.model_validate({"prep_minutes": 5, "steps": [ok["steps"][0]] + [step] * 19})
    for text in ("x" * 401, "   ", "ab"):
        with pytest.raises(ValidationError):
            model.model_validate(payload_with_text(MEAL, text))
    assert model.model_validate(payload_with_text(MEAL, "x" * 400))
    trimmed = model.model_validate(payload_with_text(MEAL, "  Stir it.  "))
    assert trimmed.steps[1].text == "Stir it."


def test_too_many_ids_in_a_step() -> None:
    payload = payload_with_text(MEAL, "Stir it.", ids=["rice"] * 26)
    with pytest.raises(ValidationError):
        recipe_draft_model(MEAL, catalog=CAT).model_validate(payload)


def _enum_values(node: Any) -> list[list[str]]:
    found: list[list[str]] = []
    if isinstance(node, dict):
        if "enum" in node:
            found.append(node["enum"])
        for value in node.values():
            found += _enum_values(value)
    elif isinstance(node, list):
        for value in node:
            found += _enum_values(value)
    return found


def test_schema_enum_lists_meal_ids() -> None:
    schema = recipe_draft_model(MEAL, catalog=CAT).model_json_schema()
    enums = _enum_values(schema)
    assert len(enums) == 1
    assert sorted(enums[0]) == sorted(meal_ids(MEAL))
    assert "milk" not in str(schema)


class RecordingFake(FakeLLMClient):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.requests: list[Request] = []

    def _send(self, request: Request) -> Response:
        self.requests.append(request)
        return super()._send(request)


def _bad() -> dict[str, Any]:
    return payload_with_text(MEAL, "Add 200 g chicken")


def test_bad_output_retried_then_rejected() -> None:
    fake = RecordingFake([_bad(), _bad(), _bad()])
    with pytest.raises(LLMOutputError):
        fake.generate(
            system="s",
            messages=[{"role": "user", "content": "r"}],
            output=recipe_draft_model(MEAL, catalog=CAT),
            max_tokens=500,
        )
    assert len(fake.requests) == 3


def test_invalid_then_valid_succeeds_with_error_text_in_second_request() -> None:
    fake = RecordingFake([_bad(), valid_payload(MEAL)])
    draft = fake.generate(
        system="s",
        messages=[{"role": "user", "content": "r"}],
        output=recipe_draft_model(MEAL, catalog=CAT),
        max_tokens=500,
    )
    assert draft.prep_minutes == 10  # type: ignore[attr-defined]
    assert len(fake.requests) == 2
    assert "states an amount" in str(fake.requests[1].messages)
