"""Prompt text for recipe steps (ADR 0008, 0025).

The templates live in ``prompts/``. Placeholders look like ``{{NAME}}`` and are filled in a
single pass, so text inserted into one placeholder is never read as another. No profile
``preferences`` and no skill level are sent (ADR 0025).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.planning.models import MealOption, Rejection

_PROMPTS = Path(__file__).parent / "prompts"
_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def _fill(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _number(quantity: float) -> str:
    """The meal's own quantity, unrounded and never in exponent form."""
    text = format(Decimal(repr(float(quantity))), "f")
    return text[:-2] if text.endswith(".0") else text


def build_recipe_system() -> str:
    return (_PROMPTS / "system.txt").read_text(encoding="utf-8")


def build_recipe_request(
    meal: MealOption,
    *,
    catalog: Catalog,
    exclusions: Iterable[ExclusionTerm],
    rejected: Sequence[Rejection],
) -> str:
    """One call's request: the meal and its lines, the exclusion labels, earlier rejections."""
    lines = [
        f"{line.ingredient_id} | {_one_line(catalog.get(line.ingredient_id).name)} | "
        f"{_number(line.quantity)} {line.unit.value}"
        for line in meal.draft.ingredients
    ]
    labels = ", ".join(t.label for t in sorted(frozenset(exclusions), key=lambda t: t.value))
    reasons = "\n".join(f"- {_one_line(r.reason)}" for r in rejected) or "(none)"
    return _fill(
        (_PROMPTS / "request.txt").read_text(encoding="utf-8"),
        {
            "NAME": _one_line(meal.draft.name),
            "DESCRIPTION": _one_line(meal.draft.description),
            "TOTAL_MINUTES": str(meal.draft.cook_minutes),
            "SERVINGS": str(meal.servings),
            "LINES": "\n".join(lines),
            "EXCLUSIONS": labels or "(none)",
            "REJECTED": reasons,
        },
    )
