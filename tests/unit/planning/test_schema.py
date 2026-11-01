"""Schema layer for model-drafted meal options (ADR 0018)."""

import copy
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mealplanner.nutrition.catalog import load_catalog
from mealplanner.planning.schema import IngredientLine, MealOptionDraft, option_batch_model

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CAT = load_catalog(FIXTURES / "catalog" / "planning.csv")
OPTIONS = FIXTURES / "llm" / "options"
MISS_LOGGER = "mealplanner.catalog_misses"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((OPTIONS / name).read_text())
    return data


def valid_option() -> dict[str, Any]:
    return fixture("valid_option.json")


def batch(*options: dict[str, Any]) -> dict[str, Any]:
    return {"options": list(options)}


def validate(payload: dict[str, Any], count: int = 1) -> Any:
    return option_batch_model(CAT, count).model_validate(payload)


def test_valid_batch_validates() -> None:
    result = validate(batch(valid_option(), valid_option()), count=2)
    assert len(result.options) == 2
    option = result.options[0]
    assert isinstance(option, MealOptionDraft)
    assert isinstance(option.ingredients[0], IngredientLine)
    assert option.name == "Chicken and rice bowl"
    assert option.ingredients[2].unit == "tbsp"


@pytest.mark.parametrize(("given", "ok"), [(2, False), (4, False), (3, True)])
def test_count_exact(given: int, ok: bool) -> None:
    payload = batch(*[valid_option() for _ in range(given)])
    if ok:
        assert len(validate(payload, count=3).options) == 3
    else:
        with pytest.raises(ValidationError):
            validate(payload, count=3)


def test_model_is_cached_per_catalog_and_count() -> None:
    assert option_batch_model(CAT, 2) is option_batch_model(CAT, 2)
    assert option_batch_model(CAT, 2) is not option_batch_model(CAT, 3)


def test_unknown_id_rejected_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=MISS_LOGGER)
    with pytest.raises(ValidationError) as exc:
        validate(fixture("unknown_id.json"))
    assert "unknown ingredient id 'gochujang'; use an id from the catalog list" in str(exc.value)
    records = [r for r in caplog.records if "catalog_miss kind=unknown_id" in r.getMessage()]
    assert len(records) == 1
    assert records[0].name == MISS_LOGGER
    assert records[0].levelno == logging.WARNING
    assert "'gochujang'" in records[0].getMessage()


@pytest.mark.parametrize(
    ("fixture_name", "ingredient", "unit"),
    [
        ("no_conversion_cup.json", "rice", "cup"),
        ("no_conversion_each.json", "chicken", "each"),
    ],
)
def test_unit_without_conversion_rejected_and_logged(
    caplog: pytest.LogCaptureFixture, fixture_name: str, ingredient: str, unit: str
) -> None:
    caplog.set_level(logging.WARNING, logger=MISS_LOGGER)
    with pytest.raises(ValidationError) as exc:
        validate(fixture(fixture_name))
    assert f"ingredient '{ingredient}' has no conversion for unit '{unit}'; use g" in str(exc.value)
    records = [r for r in caplog.records if "catalog_miss kind=no_conversion" in r.getMessage()]
    assert len(records) == 1
    assert f"id='{ingredient}'" in records[0].getMessage()
    assert f"unit={unit}" in records[0].getMessage()


def test_convertible_units_pass() -> None:
    option = valid_option()
    option["ingredients"] = [
        {"ingredient_id": "olive_oil", "quantity": 2, "unit": "tbsp"},
        {"ingredient_id": "egg", "quantity": 2, "unit": "each"},
        {"ingredient_id": "rice", "quantity": 1, "unit": "lb"},
    ]
    assert len(validate(batch(option)).options[0].ingredients) == 3


@pytest.mark.parametrize("quantity", [0, -1, float("nan"), float("inf"), "200"])
def test_quantity_invalid(quantity: object) -> None:
    option = valid_option()
    option["ingredients"][0]["quantity"] = quantity
    with pytest.raises(ValidationError):
        validate(batch(option))


@pytest.mark.parametrize("unit", ["pinch", "G"])
def test_unknown_unit_rejected(unit: str) -> None:
    option = valid_option()
    option["ingredients"][0]["unit"] = unit
    with pytest.raises(ValidationError):
        validate(batch(option))


@pytest.mark.parametrize(
    "fixture_name",
    ["nutrition_on_option.json", "nutrition_on_line.json", "nutrition_on_batch.json"],
)
def test_model_nutrition_rejected(fixture_name: str) -> None:
    with pytest.raises(ValidationError) as exc:
        validate(fixture(fixture_name))
    assert "extra_forbidden" in str(exc.value)


@pytest.mark.parametrize("fixture_name", ["day_field.json", "weekday_field.json"])
def test_day_field_rejected(fixture_name: str) -> None:
    with pytest.raises(ValidationError):
        validate(fixture(fixture_name))


def test_servings_field_rejected() -> None:
    with pytest.raises(ValidationError):
        validate(fixture("servings_field.json"))


def _mutate(path: str, value: object) -> dict[str, Any]:
    option = copy.deepcopy(valid_option())
    if value is _MISSING:
        del option[path]
    else:
        option[path] = value
    return batch(option)


_MISSING = object()
_LINE = {"ingredient_id": "rice", "quantity": 1, "unit": "g"}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_mutate("name", _MISSING), id="no-name"),
        pytest.param(_mutate("cook_minutes", "30"), id="cook-minutes-string"),
        pytest.param(_mutate("cook_minutes", 0), id="cook-minutes-zero"),
        pytest.param(_mutate("cook_minutes", 241), id="cook-minutes-241"),
        pytest.param(_mutate("ingredients", []), id="no-ingredients"),
        pytest.param(_mutate("ingredients", [_LINE] * 26), id="26-ingredients"),
        pytest.param(_mutate("tags", ["a"] * 7), id="7-tags"),
        pytest.param(_mutate("tags", ["Spicy Food"]), id="bad-tag"),
        pytest.param(_mutate("tags", ["a" * 25]), id="long-tag"),
        pytest.param(_mutate("name", "x" * 81), id="name-81"),
        pytest.param(_mutate("name", "ab"), id="name-2"),
        pytest.param(_mutate("name", "   \n "), id="name-whitespace"),
        pytest.param(_mutate("description", ""), id="description-empty"),
        pytest.param(_mutate("description", "d" * 301), id="description-301"),
    ],
)
def test_missing_and_mistyped_fields(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "x" * 80),
        ("name", "abc"),
        ("cook_minutes", 1),
        ("cook_minutes", 240),
        ("tags", ["a"] * 6),
        ("tags", []),
        ("tags", ["a" * 24]),
        ("ingredients", [_LINE] * 25),
        ("description", "d" * 300),
    ],
)
def test_boundaries_accepted(field: str, value: object) -> None:
    validate(_mutate(field, value))


def test_name_is_trimmed() -> None:
    result = validate(_mutate("name", "  Rice bowl \n"))
    assert result.options[0].name == "Rice bowl"


def test_drafts_are_not_constructible_with_nutrition() -> None:
    with pytest.raises(ValidationError):
        MealOptionDraft.model_validate({**valid_option(), "kcal": 5})


def _property_names(node: object) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            names.update(props)
        for value in node.values():
            names |= _property_names(value)
    elif isinstance(node, list):
        for value in node:
            names |= _property_names(value)
    return names


def _enums_for(node: object, prop: str) -> list[list[str]]:
    found: list[list[str]] = []
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and prop in props:
            found.append(props[prop].get("enum"))
        for value in node.values():
            found += _enums_for(value, prop)
    elif isinstance(node, list):
        for value in node:
            found += _enums_for(value, prop)
    return found


def test_json_schema_has_id_enum() -> None:
    schema = option_batch_model(CAT, 2).model_json_schema()
    enums = _enums_for(schema, "ingredient_id")
    assert enums == [list(CAT.ids())]
    names = {n.lower() for n in _property_names(schema)}
    assert names  # the walk found the properties
    for forbidden in ("calorie", "kcal", "protein", "carb", "fat", "day", "servings"):
        assert not [n for n in names if forbidden in n], forbidden


def test_json_schema_count_is_exact() -> None:
    schema = option_batch_model(CAT, 3).model_json_schema()
    options = schema["properties"]["options"]
    assert options["minItems"] == 3
    assert options["maxItems"] == 3


def test_log_line_is_sanitized(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=MISS_LOGGER)
    option = valid_option()
    bad = "x" * 250 + "\n" + "y" * 250
    option["ingredients"][0]["ingredient_id"] = bad
    with pytest.raises(ValidationError):
        validate(batch(option))
    (record,) = [r for r in caplog.records if "catalog_miss kind=unknown_id" in r.getMessage()]
    message = record.getMessage()
    assert "\n" not in message
    prefix = "catalog_miss kind=unknown_id id="
    assert message.startswith(prefix)
    logged_id = message[len(prefix) :]
    assert len(logged_id) <= 64
    assert logged_id.startswith("'xxx")
