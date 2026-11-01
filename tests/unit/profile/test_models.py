from typing import Any

import pytest
from pydantic import ValidationError

from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.profile.models import DEFAULT_PROFILE, Profile


def make(**overrides: Any) -> Profile:
    data: dict[str, Any] = {
        "exclusions": [],
        "servings": 2,
        "max_cook_minutes": 45,
        "skill": "intermediate",
        "repeat_after_weeks": 2,
        "preferences": "",
    }
    data.update(overrides)
    return Profile(**data)


def test_default_profile() -> None:
    assert DEFAULT_PROFILE == make()
    assert DEFAULT_PROFILE.exclusions == frozenset()


def test_valid_profile_round_trips_json() -> None:
    p = make(exclusions=["soy", ExclusionTerm.meat], preferences="likes thai\nno cilantro")
    assert Profile.model_validate_json(p.model_dump_json()) == p


def test_hashable() -> None:
    assert hash(make(exclusions=["soy"])) == hash(make(exclusions=["soy"]))


@pytest.mark.parametrize("value", [1, 2, 12])
def test_servings_accepted(value: int) -> None:
    assert make(servings=value).servings == value


@pytest.mark.parametrize("value", [0, -1, 13, 2.5, 2.0, True, "4"])
def test_servings_bounds(value: Any) -> None:
    with pytest.raises(ValidationError):
        make(servings=value)


@pytest.mark.parametrize("value", [1, 240])
def test_max_cook_minutes_accepted(value: int) -> None:
    assert make(max_cook_minutes=value).max_cook_minutes == value


@pytest.mark.parametrize("value", [0, -5, 241, 30.5, "30", None])
def test_max_cook_minutes_bounds(value: Any) -> None:
    with pytest.raises(ValidationError):
        make(max_cook_minutes=value)


@pytest.mark.parametrize("value", [0, 12])
def test_repeat_after_weeks_accepted(value: int) -> None:
    assert make(repeat_after_weeks=value).repeat_after_weeks == value


@pytest.mark.parametrize("value", [-1, 13, 1.5, True, "2"])
def test_repeat_after_weeks_bounds(value: Any) -> None:
    with pytest.raises(ValidationError):
        make(repeat_after_weeks=value)


@pytest.mark.parametrize("value", ["beginner", "intermediate", "advanced"])
def test_skill_accepted(value: str) -> None:
    assert make(skill=value).skill == value


@pytest.mark.parametrize("value", ["expert", "", "Beginner"])
def test_skill_closed_set(value: str) -> None:
    with pytest.raises(ValidationError):
        make(skill=value)


def test_unknown_exclusion_rejected() -> None:
    with pytest.raises(ValidationError, match="cilantro"):
        make(exclusions=["peanuts", "cilantro"])


def test_exclusions_accept_enum_values_and_strings_and_iterables() -> None:
    expected = frozenset({ExclusionTerm.soy, ExclusionTerm.milk})
    assert make(exclusions=["soy", ExclusionTerm.milk]).exclusions == expected
    assert make(exclusions=("soy", "milk")).exclusions == expected
    assert make(exclusions={"soy", "milk"}).exclusions == expected
    assert make(exclusions=(t for t in ("soy", "milk"))).exclusions == expected


def test_exclusions_from_json() -> None:
    raw = (
        '{"exclusions": ["soy", "milk"], "servings": 2, "max_cook_minutes": 45,'
        ' "skill": "beginner", "repeat_after_weeks": 0, "preferences": ""}'
    )
    assert Profile.model_validate_json(raw).exclusions == {ExclusionTerm.soy, ExclusionTerm.milk}


def test_exclusions_not_a_bare_string() -> None:
    with pytest.raises(ValidationError):
        make(exclusions="soy")


def test_exclusions_deduplicated_and_sorted_on_dump() -> None:
    p = make(exclusions=["soy", "milk", "soy"])
    assert p.model_dump(mode="json")["exclusions"] == ["milk", "soy"]
    assert '"exclusions":["milk","soy"]' in p.model_dump_json()


def test_other_fields_remain_strict() -> None:
    with pytest.raises(ValidationError):
        make(preferences=5)


def test_frozen() -> None:
    with pytest.raises(ValidationError):
        make().servings = 3  # type: ignore[misc]


def test_preferences_trimmed_and_newlines_normalized() -> None:
    assert make(preferences="  a\r\nb\rc  ").preferences == "a\nb\nc"


def test_preferences_length_cap() -> None:
    assert make(preferences="x" * 4000).preferences == "x" * 4000
    with pytest.raises(ValidationError):
        make(preferences="x" * 4001)
    assert make(preferences="  \n" + "x" * 4000 + " \r\n ").preferences == "x" * 4000


def test_empty_preferences_allowed() -> None:
    assert make(preferences="").preferences == ""
    assert make(preferences="   \r\n ").preferences == ""


@pytest.mark.parametrize("field", ["calorie_target", "protein_target_g", "foo"])
def test_extra_field_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        make(**{field: 100})


def test_no_target_fields() -> None:
    banned = ("calorie", "kcal", "protein", "carb", "fat", "macro", "target")
    for name in Profile.model_fields:
        assert not any(b in name for b in banned), name
