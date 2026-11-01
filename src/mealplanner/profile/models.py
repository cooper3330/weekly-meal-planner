"""The user profile: a strict, frozen, validated record (ADR 0017)."""

from collections.abc import Iterable
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    field_validator,
)

from mealplanner.nutrition.exclusions import ExclusionTerm

PREFERENCES_MAX_CHARS = 4000


def _normalize_preferences(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return value


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    # Lax on purpose: accepts any iterable of terms or plain strings, from Python or JSON.
    exclusions: Annotated[frozenset[ExclusionTerm], Field(strict=False)]
    servings: Annotated[int, Field(ge=1, le=12)]
    max_cook_minutes: Annotated[int, Field(ge=1, le=240)]
    skill: Literal["beginner", "intermediate", "advanced"]
    repeat_after_weeks: Annotated[int, Field(ge=0, le=12)]
    # Trimming happens in the before-validator so the length cap applies after trimming.
    preferences: Annotated[str, StringConstraints(max_length=PREFERENCES_MAX_CHARS)]

    @field_validator("exclusions", mode="before")
    @classmethod
    def _coerce_exclusions(cls, value: Any) -> Any:
        if isinstance(value, (str, bytes, dict)) or not isinstance(value, Iterable):
            raise ValueError("exclusions must be a list of exclusion terms")
        terms = []
        for item in value:
            try:
                terms.append(ExclusionTerm(item))  # type: ignore[call-arg]
            except ValueError:
                raise ValueError(f"unknown exclusion term: {item!r}") from None
        return frozenset(terms)

    @field_validator("preferences", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        return _normalize_preferences(value)

    @field_serializer("exclusions")
    def _serialize_exclusions(self, value: frozenset[ExclusionTerm]) -> list[str]:
        return sorted(t.value for t in value)


class ProfileVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    created_at: str
    profile: Profile


class ProfileVersionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    created_at: str


DEFAULT_PROFILE = Profile(
    exclusions=frozenset(),
    servings=2,
    max_cook_minutes=45,
    skill="intermediate",
    repeat_after_weeks=2,
    preferences="",
)
