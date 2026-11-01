"""Prompt text for slate generation (ADR 0008, 0019).

The templates live in ``prompts/``. Placeholders look like ``{{NAME}}`` and are filled in a
single pass, so text inserted into one placeholder is never read as another.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import MealType, Rejection
from mealplanner.profile.models import Profile

_PROMPTS = Path(__file__).parent / "prompts"
_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
_PREFERENCES_TAG = re.compile(r"<(?=\s*/?\s*user_preferences)", re.IGNORECASE)


def _fill(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def build_system_prompt(catalog: Catalog, exclusions: Iterable[ExclusionTerm]) -> str:
    """The fixed rules, and ``id | name`` for each catalog row not tagged with an exclusion.

    Leaving rows out only saves retries; ``check_option`` is the guarantee.
    """
    excluded = frozenset(exclusions)
    lines = [
        f"{row.id} | {_one_line(row.name)}"
        for row in catalog.rows()
        if not ((row.allergens | row.diet_tags) & excluded)
    ]
    template = (_PROMPTS / "system.txt").read_text(encoding="utf-8")
    return _fill(template, {"CATALOG": "\n".join(lines)})


def _escape_preferences(text: str) -> str:
    """Make any ``<user_preferences>`` or closing tag in the text unable to close the block."""
    return _PREFERENCES_TAG.sub("&lt;", text)


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {_one_line(item)}" for item in items) if items else "(none)"


def build_request(
    meal_type: MealType,
    count: int,
    profile: Profile,
    avoid: Sequence[str],
    rejected: Sequence[Rejection],
) -> str:
    """One call's request: the meal type and count, the profile, and what to avoid."""
    labels = ", ".join(t.label for t in sorted(profile.exclusions, key=lambda t: t.value))
    return _fill(
        (_PROMPTS / "request.txt").read_text(encoding="utf-8"),
        {
            "COUNT": str(count),
            "MEAL_TYPE": meal_type.value,
            "SERVINGS": str(profile.servings),
            "MAX_COOK_MINUTES": str(profile.max_cook_minutes),
            "SKILL": profile.skill,
            "EXCLUSIONS": labels or "(none)",
            "AVOID": _bullets(avoid),
            "REJECTED": _bullets([f"{r.name}: {r.reason}" for r in rejected]),
            "PREFERENCES": _escape_preferences(profile.preferences),
        },
    )
