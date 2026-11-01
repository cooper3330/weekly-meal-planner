"""The ingredient catalog: a read-only CSV of per-100 g nutrition and tags (ADR 0007, 0020).

Core module: imports nothing from ``planning/``, ``profile/``, ``web/`` or ``llm/``.
"""

import csv
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from mealplanner.nutrition.exclusions import ExclusionTerm

_TERMS_BY_VALUE: dict[str, ExclusionTerm] = {t.value: t for t in ExclusionTerm}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_REQUIRED_COLUMNS = (
    "id",
    "name",
    "aliases",
    "fdc_id",
    "kcal_per_100g",
    "protein_g_per_100g",
    "carbs_g_per_100g",
    "fat_g_per_100g",
    "g_per_ml",
    "g_per_each",
    "allergens",
    "diet_tags",
)


class CatalogError(ValueError):
    """The catalog file is malformed. The message names the line and column."""


@dataclass(frozen=True)
class CatalogRow:
    id: str
    name: str
    aliases: tuple[str, ...]
    fdc_id: int
    kcal_per_100g: float
    protein_g_per_100g: float
    carbs_g_per_100g: float
    fat_g_per_100g: float
    g_per_ml: float | None
    g_per_each: float | None
    allergens: frozenset[ExclusionTerm]
    diet_tags: frozenset[ExclusionTerm]


@dataclass(frozen=True)
class Catalog:
    _rows: tuple[CatalogRow, ...]

    def get(self, id: str) -> CatalogRow:
        for row in self._rows:
            if row.id == id:
                return row
        raise KeyError(id)

    def __contains__(self, id: object) -> bool:
        return any(row.id == id for row in self._rows)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(row.id for row in self._rows))

    def rows(self) -> tuple[CatalogRow, ...]:
        return self._rows


def _default_catalog_path() -> Path:
    """Location of the packaged catalog (replaced in tests)."""
    return Path(str(resources.files("mealplanner.nutrition") / "data" / "ingredients.csv"))


def _number(raw: str, line: int, column: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise CatalogError(f"line {line}, column {column}: not a number: {raw!r}") from None
    if not math.isfinite(value):
        raise CatalogError(f"line {line}, column {column}: must be finite, got {raw!r}")
    return value


def _nonneg(raw: str, line: int, column: str) -> float:
    value = _number(raw, line, column)
    if value < 0:
        raise CatalogError(f"line {line}, column {column}: must not be negative, got {raw!r}")
    return value


def _optional_positive(raw: str, line: int, column: str) -> float | None:
    if raw.strip() == "":
        return None
    value = _number(raw, line, column)
    if value <= 0:
        raise CatalogError(f"line {line}, column {column}: must be positive, got {raw!r}")
    return value


def _pipe_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split("|") if part.strip()]


def _terms(raw: str, line: int, column: str, kind: str) -> frozenset[ExclusionTerm]:
    terms: set[ExclusionTerm] = set()
    for part in _pipe_list(raw):
        try:
            term = _TERMS_BY_VALUE[part]
        except KeyError:
            raise CatalogError(
                f"line {line}, column {column}: unknown exclusion term {part!r}"
            ) from None
        if term.kind != kind:
            raise CatalogError(
                f"line {line}, column {column}: {part!r} is a {term.kind} term, not {kind}"
            )
        terms.add(term)
    return frozenset(terms)


def _rows(reader: csv.DictReader[str]) -> Iterator[CatalogRow]:
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()
    for record in reader:
        line = reader.line_num
        for column in _REQUIRED_COLUMNS:
            if record.get(column) is None:
                raise CatalogError(f"line {line}, column {column}: missing value")
        id = record["id"].strip()
        if not _ID_RE.match(id):
            raise CatalogError(f"line {line}, column id: not a valid slug: {id!r}")
        if id in seen_ids:
            raise CatalogError(f"line {line}, column id: duplicate id {id!r}")
        seen_ids.add(id)
        aliases = tuple(_pipe_list(record["aliases"]))
        for alias in aliases:
            if alias in seen_aliases:
                raise CatalogError(f"line {line}, column aliases: duplicate alias {alias!r}")
            seen_aliases.add(alias)
        try:
            fdc_id = int(record["fdc_id"])
        except ValueError:
            raise CatalogError(
                f"line {line}, column fdc_id: not an integer: {record['fdc_id']!r}"
            ) from None
        yield CatalogRow(
            id=id,
            name=record["name"].strip(),
            aliases=aliases,
            fdc_id=fdc_id,
            kcal_per_100g=_nonneg(record["kcal_per_100g"], line, "kcal_per_100g"),
            protein_g_per_100g=_nonneg(record["protein_g_per_100g"], line, "protein_g_per_100g"),
            carbs_g_per_100g=_nonneg(record["carbs_g_per_100g"], line, "carbs_g_per_100g"),
            fat_g_per_100g=_nonneg(record["fat_g_per_100g"], line, "fat_g_per_100g"),
            g_per_ml=_optional_positive(record["g_per_ml"], line, "g_per_ml"),
            g_per_each=_optional_positive(record["g_per_each"], line, "g_per_each"),
            allergens=_terms(record["allergens"], line, "allergens", "allergen"),
            diet_tags=_terms(record["diet_tags"], line, "diet_tags", "diet"),
        )


def load_catalog(path: Path | None = None) -> Catalog:
    """Load and validate a catalog CSV; the packaged one when ``path`` is None."""
    if path is None:
        path = _default_catalog_path()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for column in _REQUIRED_COLUMNS:
            if column not in fieldnames:
                raise CatalogError(f"line 1, column {column}: missing column")
        return Catalog(tuple(_rows(reader)))
