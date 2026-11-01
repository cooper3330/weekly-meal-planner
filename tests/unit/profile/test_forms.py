import pytest

from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.profile.forms import FormErrors, parse_profile_form
from mealplanner.profile.models import Profile

SERVINGS_MSG = "Servings must be a whole number from 1 to 12."
COOK_MSG = "Max cook time must be a whole number of minutes from 1 to 240."
REPEAT_MSG = "Repeat after weeks must be a whole number from 0 to 12."
PREFS_MSG = "Preferences can be at most 4000 characters."


def good(**over: list[str]) -> dict[str, list[str]]:
    form = {
        "servings": ["3"],
        "max_cook_minutes": ["30"],
        "skill": ["advanced"],
        "repeat_after_weeks": ["4"],
        "preferences": ["likes spicy food"],
        "exclusions": ["milk", "pork"],
    }
    form.update(over)
    return form


def errors_of(form: dict[str, list[str]]) -> dict[str, str]:
    result = parse_profile_form(form)
    assert isinstance(result, FormErrors)
    return result.messages


def test_valid_form() -> None:
    result = parse_profile_form(good())
    assert result == Profile(
        exclusions=frozenset({ExclusionTerm.milk, ExclusionTerm.pork}),
        servings=3,
        max_cook_minutes=30,
        skill="advanced",
        repeat_after_weeks=4,
        preferences="likes spicy food",
    )


def test_no_exclusions_ticked() -> None:
    form = good()
    del form["exclusions"]
    result = parse_profile_form(form)
    assert isinstance(result, Profile)
    assert result.exclusions == frozenset()


@pytest.mark.parametrize("value", ["", "abc", "0", "-2", "2.5", "1e2", "13", "٣"])
def test_servings_rejected(value: str) -> None:
    assert errors_of(good(servings=[value])) == {"servings": SERVINGS_MSG}


def test_servings_missing() -> None:
    form = good()
    del form["servings"]
    assert errors_of(form) == {"servings": SERVINGS_MSG}


def test_servings_trimmed_and_sign() -> None:
    result = parse_profile_form(good(servings=[" 4 "]))
    assert isinstance(result, Profile) and result.servings == 4
    result = parse_profile_form(good(servings=["+4"]))
    assert isinstance(result, Profile) and result.servings == 4


@pytest.mark.parametrize("value", ["", "abc", "0", "241", "30.0", "-1"])
def test_cook_minutes_rejected(value: str) -> None:
    assert errors_of(good(max_cook_minutes=[value])) == {"max_cook_minutes": COOK_MSG}


def test_cook_minutes_missing() -> None:
    form = good()
    del form["max_cook_minutes"]
    assert errors_of(form) == {"max_cook_minutes": COOK_MSG}


def test_repeat_zero_accepted() -> None:
    result = parse_profile_form(good(repeat_after_weeks=["0"]))
    assert isinstance(result, Profile) and result.repeat_after_weeks == 0


@pytest.mark.parametrize("value", ["-1", "13", "x"])
def test_repeat_rejected(value: str) -> None:
    assert errors_of(good(repeat_after_weeks=[value])) == {"repeat_after_weeks": REPEAT_MSG}


def test_skill_missing_or_unknown() -> None:
    form = good()
    del form["skill"]
    assert list(errors_of(form)) == ["skill"]
    assert list(errors_of(good(skill=["expert"]))) == ["skill"]


def test_unknown_exclusion_is_error() -> None:
    assert list(errors_of(good(exclusions=["milk", "cilantro"]))) == ["exclusions"]


def test_duplicate_exclusion_accepted_once() -> None:
    result = parse_profile_form(good(exclusions=["milk", "milk"]))
    assert isinstance(result, Profile)
    assert result.exclusions == frozenset({ExclusionTerm.milk})


def test_several_bad_fields() -> None:
    messages = errors_of(good(servings=["0"], max_cook_minutes=["x"], repeat_after_weeks=["99"]))
    assert messages == {
        "servings": SERVINGS_MSG,
        "max_cook_minutes": COOK_MSG,
        "repeat_after_weeks": REPEAT_MSG,
    }


def test_preferences_too_long() -> None:
    assert errors_of(good(preferences=["x" * 4001])) == {"preferences": PREFS_MSG}


def test_preferences_cap_counts_after_crlf_and_trim() -> None:
    text = "  " + "a\r\n" * 1333 + "a" + "  "  # 3999 characters once normalized and trimmed
    result = parse_profile_form(good(preferences=[text]))
    assert isinstance(result, Profile)
    assert "\r" not in result.preferences


def test_extra_fields_ignored() -> None:
    result = parse_profile_form(good(calorie_target=["2000"]))
    assert isinstance(result, Profile)
    assert not hasattr(result, "calorie_target")
    assert "calorie_target" not in result.model_dump()


def test_messages_are_not_raw_pydantic() -> None:
    for message in errors_of(good(servings=["0"], skill=["x"], exclusions=["zzz"])).values():
        assert "validation error" not in message.lower()
        assert "pydantic" not in message.lower()
