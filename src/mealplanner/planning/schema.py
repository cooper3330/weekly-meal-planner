"""What the model may return for a meal option (ADR 0018).

Every model forbids extra fields, so a model-supplied number, serving count or day is a
schema failure that the client retries. Catalog membership is checked here, inside the
retry loop, and every miss is logged.
"""

import logging
from functools import cache
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.units import Unit, can_convert

miss_log = logging.getLogger("mealplanner.catalog_misses")

_ID_LOG_CHARS = 64


def _logged(value: object) -> str:
    """The model's text as one safe log token: repr (escapes newlines), cut to 64 chars."""
    return repr(value)[:_ID_LOG_CHARS]


class IngredientLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_id: str
    quantity: Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
    unit: Unit


class MealOptionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=80)]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    cook_minutes: Annotated[int, Field(ge=1, le=240, strict=True)]
    tags: Annotated[
        list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,23}$")]],
        Field(max_length=6),
    ]
    ingredients: Annotated[list[IngredientLine], Field(min_length=1, max_length=25)]


@cache
def option_batch_model(catalog: Catalog, count: int) -> type[BaseModel]:
    """An ``OptionBatch`` model for exactly ``count`` options, ids bound to ``catalog``."""
    ids = catalog.ids()

    class CatalogIngredientLine(IngredientLine):
        # A plain str with a before-validator, not a Literal: the miss is logged, and the
        # error does not list every id. The JSON schema still carries the closed set.
        ingredient_id: str = Field(json_schema_extra={"enum": list(ids)})

        @field_validator("ingredient_id", mode="before")
        @classmethod
        def _known_id(cls, value: Any) -> Any:
            if not isinstance(value, str) or value not in catalog:
                miss_log.warning("catalog_miss kind=unknown_id id=%s", _logged(value))
                raise ValueError(
                    f"unknown ingredient id {_logged(value)}; use an id from the catalog list"
                )
            return value

        @model_validator(mode="after")
        def _unit_converts(self) -> "CatalogIngredientLine":
            if not can_convert(self.unit, catalog.get(self.ingredient_id)):
                miss_log.warning(
                    "catalog_miss kind=no_conversion id=%s unit=%s",
                    _logged(self.ingredient_id),
                    self.unit.value,
                )
                raise ValueError(
                    f"ingredient {self.ingredient_id!r} has no conversion for unit "
                    f"{self.unit.value!r}; use g"
                )
            return self

    CatalogIngredientLine.__name__ = "IngredientLine"
    CatalogIngredientLine.__qualname__ = "IngredientLine"

    class CatalogMealOption(MealOptionDraft):
        ingredients: Annotated[list[CatalogIngredientLine], Field(min_length=1, max_length=25)]  # type: ignore[assignment]

    CatalogMealOption.__name__ = "MealOptionDraft"
    CatalogMealOption.__qualname__ = "MealOptionDraft"

    class OptionBatch(BaseModel):
        model_config = ConfigDict(extra="forbid")

        options: Annotated[list[CatalogMealOption], Field(min_length=count, max_length=count)]

    return OptionBatch
