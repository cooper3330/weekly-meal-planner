"""Parse the profile form into a ``Profile`` or per-field messages. No web imports.

Messages are written here; pydantic's are never shown to the user.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.profile.models import PREFERENCES_MAX_CHARS, Profile, _normalize_preferences

SKILLS = ("beginner", "intermediate", "advanced")

_WHOLE_NUMBER = re.compile(r"[+-]?[0-9]+")

SERVINGS_MSG = "Servings must be a whole number from 1 to 12."
COOK_MSG = "Max cook time must be a whole number of minutes from 1 to 240."
REPEAT_MSG = "Repeat after weeks must be a whole number from 0 to 12."
SKILL_MSG = "Choose a cooking skill: beginner, intermediate or advanced."
EXCLUSIONS_MSG = "Choose exclusions only from the list shown."
PREFERENCES_MSG = f"Preferences can be at most {PREFERENCES_MAX_CHARS} characters."
GENERAL_MSG = "The profile could not be saved. Check the fields and try again."


@dataclass(frozen=True)
class FormErrors:
    """Messages keyed by field name; the key ``form`` is for problems with no single field."""

    messages: dict[str, str]


def _first(form: Mapping[str, list[str]], name: str) -> str:
    values = form.get(name)
    return values[0] if values else ""


def _whole_number(raw: str, low: int, high: int) -> int | None:
    text = raw.strip()
    if not _WHOLE_NUMBER.fullmatch(text):
        return None
    number = int(text)
    return number if low <= number <= high else None


def parse_profile_form(form: Mapping[str, list[str]]) -> Profile | FormErrors:
    """Read only the known field names; anything else posted is ignored."""
    messages: dict[str, str] = {}

    servings = _whole_number(_first(form, "servings"), 1, 12)
    if servings is None:
        messages["servings"] = SERVINGS_MSG
    cook = _whole_number(_first(form, "max_cook_minutes"), 1, 240)
    if cook is None:
        messages["max_cook_minutes"] = COOK_MSG
    repeat = _whole_number(_first(form, "repeat_after_weeks"), 0, 12)
    if repeat is None:
        messages["repeat_after_weeks"] = REPEAT_MSG

    skill = _first(form, "skill")
    if skill not in SKILLS:
        messages["skill"] = SKILL_MSG

    terms: set[ExclusionTerm] = set()
    for value in form.get("exclusions", []):
        try:
            terms.add(ExclusionTerm(value))  # type: ignore[call-arg]
        except ValueError:
            messages["exclusions"] = EXCLUSIONS_MSG

    preferences = _normalize_preferences(_first(form, "preferences"))
    if len(preferences) > PREFERENCES_MAX_CHARS:
        messages["preferences"] = PREFERENCES_MSG

    if messages:
        return FormErrors(messages)
    try:
        return Profile.model_validate(
            {
                "exclusions": terms,
                "servings": servings,
                "max_cook_minutes": cook,
                "skill": skill,
                "repeat_after_weeks": repeat,
                "preferences": preferences,
            }
        )
    except ValidationError:
        return FormErrors({"form": GENERAL_MSG})
