"""Developer tool that builds the ingredient catalog (ADR 0020). Never run by the app.

    python -m mealplanner.nutrition.build_catalog extract --fdc-dir DIR
    python -m mealplanner.nutrition.build_catalog build

``extract`` copies nutrient values from a downloaded USDA SR Legacy CSV directory into
``fdc_extract.csv`` for the ``fdc_id`` values in ``catalog_source.csv``. ``build`` joins the
source and the extract into ``ingredients.csv``. Standard library only; no network.
"""

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_SOURCE = DATA_DIR / "catalog_source.csv"
DEFAULT_EXTRACT = DATA_DIR / "fdc_extract.csv"
DEFAULT_OUT = DATA_DIR / "ingredients.csv"

# Output column -> FDC nutrient id (ADR 0020).
NUTRIENTS = {
    "kcal_per_100g": "1008",  # energy, kcal
    "protein_g_per_100g": "1003",
    "carbs_g_per_100g": "1005",  # carbohydrate, by difference
    "fat_g_per_100g": "1004",  # total lipid
}
EXTRACT_COLUMNS = ["fdc_id", "description", *NUTRIENTS]
SOURCE_COLUMNS = [
    "id",
    "name",
    "aliases",
    "fdc_id",
    "g_per_ml",
    "g_per_each",
    "allergens",
    "diet_tags",
    "conversion_note",
]
CATALOG_COLUMNS = [
    "id",
    "name",
    "aliases",
    "fdc_id",
    *NUTRIENTS,
    "g_per_ml",
    "g_per_each",
    "allergens",
    "diet_tags",
    "conversion_note",
]


class BuildError(Exception):
    """A user-facing failure; the message is printed and the exit status is 1."""


def _read(path: Path, required: Sequence[str]) -> list[dict[str, str]]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            missing = [c for c in required if c not in (reader.fieldnames or [])]
            if missing:
                raise BuildError(f"{path}: missing column(s) {', '.join(missing)}")
            return list(reader)
    except OSError as e:
        raise BuildError(f"cannot read {path}: {e.strerror}") from None


def _write(path: Path, columns: Sequence[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _source_fdc_ids(source: Path) -> list[str]:
    ids = {row["fdc_id"].strip() for row in _read(source, SOURCE_COLUMNS)}
    return sorted(ids, key=int)


def extract(fdc_dir: Path, source: Path, out: Path) -> None:
    wanted = set(_source_fdc_ids(source))
    food_path = fdc_dir / "food.csv"
    descriptions = {
        row["fdc_id"]: row["description"]
        for row in _read(food_path, ["fdc_id", "description"])
        if row["fdc_id"] in wanted
    }
    unknown = sorted(wanted - descriptions.keys(), key=int)
    if unknown:
        raise BuildError(f"fdc_id not found in {food_path}: {', '.join(unknown)}")
    wanted_nutrients = set(NUTRIENTS.values())
    amounts: dict[str, dict[str, str]] = {fdc_id: {} for fdc_id in wanted}
    for row in _read(fdc_dir / "food_nutrient.csv", ["fdc_id", "nutrient_id", "amount"]):
        if row["fdc_id"] in amounts and row["nutrient_id"] in wanted_nutrients:
            amounts[row["fdc_id"]][row["nutrient_id"]] = row["amount"]
    rows: list[dict[str, str]] = []
    for fdc_id in sorted(wanted, key=int):
        record = {"fdc_id": fdc_id, "description": descriptions[fdc_id]}
        for column, nutrient_id in NUTRIENTS.items():
            if amounts[fdc_id].get(nutrient_id, "") == "":
                raise BuildError(f"fdc_id {fdc_id} has no value for nutrient {nutrient_id}")
            record[column] = amounts[fdc_id][nutrient_id]
        rows.append(record)
    _write(out, EXTRACT_COLUMNS, rows)


def build(source: Path, extract_path: Path, out: Path) -> None:
    extracted = {row["fdc_id"]: row for row in _read(extract_path, EXTRACT_COLUMNS)}
    rows: list[dict[str, str]] = []
    for row in _read(source, SOURCE_COLUMNS):
        fdc_id = row["fdc_id"].strip()
        if fdc_id not in extracted:
            raise BuildError(
                f"{row['id']}: fdc_id {fdc_id} is not in {extract_path}; run the extract step"
            )
        record = {column: row[column] for column in SOURCE_COLUMNS}
        record["fdc_id"] = fdc_id
        for column in NUTRIENTS:
            record[column] = extracted[fdc_id][column]
        rows.append(record)
    rows.sort(key=lambda r: r["id"])
    _write(out, CATALOG_COLUMNS, rows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m mealplanner.nutrition.build_catalog")
    steps = parser.add_subparsers(dest="step", required=True)
    ext = steps.add_parser("extract", help="copy nutrients from an SR Legacy CSV directory")
    ext.add_argument("--fdc-dir", type=Path, required=True)
    ext.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ext.add_argument("--out", type=Path, default=DEFAULT_EXTRACT)
    bld = steps.add_parser("build", help="join the source and the extract into ingredients.csv")
    bld.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    bld.add_argument("--extract", type=Path, default=DEFAULT_EXTRACT)
    bld.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.step == "extract":
            extract(args.fdc_dir, args.source, args.out)
        else:
            build(args.source, args.extract, args.out)
    except BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
