"""The catalog converter (ADR 0020), run against a tiny fake SR Legacy directory."""

import csv
import shutil
from pathlib import Path

import pytest

from mealplanner.nutrition import build_catalog
from mealplanner.nutrition.catalog import load_catalog

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "fdc"
GHOST_ROW = "ghost,Ghost,,123456,,,,,\n"

EXTRACT_HEADER = [
    "fdc_id",
    "description",
    "kcal_per_100g",
    "protein_g_per_100g",
    "carbs_g_per_100g",
    "fat_g_per_100g",
]
CATALOG_HEADER = [
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
    "conversion_note",
]


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _source_with_ghost(tmp_path: Path) -> Path:
    source = tmp_path / "source_ghost.csv"
    source.write_text((FIXTURES / "source.csv").read_text() + GHOST_ROW, encoding="utf-8")
    return source


def _extract(
    tmp_path: Path, source: Path = FIXTURES / "source.csv", fdc: Path = FIXTURES
) -> tuple[int, Path]:
    out = tmp_path / "fdc_extract.csv"
    code = build_catalog.main(
        ["extract", "--fdc-dir", str(fdc), "--source", str(source), "--out", str(out)]
    )
    return code, out


def test_extract_picks_requested_nutrients(tmp_path: Path) -> None:
    code, out = _extract(tmp_path)
    assert code == 0
    rows = _rows(out)
    # Sorted by fdc_id, only the source's ids (900009 is skipped).
    assert [r["fdc_id"] for r in rows] == ["900001", "900002", "900003"]
    # Energy comes from 1008, not the 2047 rows (121 and 365); fiber (1079) is ignored.
    assert rows[0] == {
        "fdc_id": "900001",
        "description": "Chicken, breast, raw",
        "kcal_per_100g": "120",
        "protein_g_per_100g": "22.5",
        "carbs_g_per_100g": "0",
        "fat_g_per_100g": "2.62",
    }
    assert rows[2]["kcal_per_100g"] == "360"
    assert rows[2]["carbs_g_per_100g"] == "79.3"


def test_extract_header_and_line_endings(tmp_path: Path) -> None:
    _, out = _extract(tmp_path)
    raw = out.read_bytes()
    assert raw.startswith((",".join(EXTRACT_HEADER) + "\n").encode())
    assert b"\r" not in raw


def test_extract_missing_fdc_id_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _extract(tmp_path, source=_source_with_ghost(tmp_path))
    assert code != 0
    assert "123456" in capsys.readouterr().err
    assert not out.exists()


def test_extract_missing_nutrient_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fdc = tmp_path / "fdc"
    shutil.copytree(FIXTURES, fdc)
    lines = (fdc / "food_nutrient.csv").read_text().splitlines(keepends=True)
    # Drop the protein (1003) row of 900001.
    kept = [ln for ln in lines if not ln.startswith('"3","900001"')]
    assert len(kept) == len(lines) - 1
    (fdc / "food_nutrient.csv").write_text("".join(kept))
    code, out = _extract(tmp_path, fdc=fdc)
    assert code != 0
    err = capsys.readouterr().err
    assert "900001" in err
    assert "1003" in err
    assert not out.exists()


def test_extract_missing_fdc_dir_fails(tmp_path: Path) -> None:
    code, out = _extract(tmp_path, fdc=tmp_path / "nowhere")
    assert code != 0
    assert not out.exists()


def _build(
    tmp_path: Path, source: Path = FIXTURES / "source.csv", name: str = "out.csv"
) -> tuple[int, Path]:
    out = tmp_path / name
    code = build_catalog.main(
        [
            "build",
            "--source",
            str(source),
            "--extract",
            str(FIXTURES / "extract.csv"),
            "--out",
            str(out),
        ]
    )
    return code, out


def test_build_joins_and_sorts(tmp_path: Path) -> None:
    code, out = _build(tmp_path)
    assert code == 0
    raw = out.read_bytes()
    assert raw.startswith((",".join(CATALOG_HEADER) + "\n").encode())
    assert b"\r" not in raw
    rows = _rows(out)
    assert [r["id"] for r in rows] == ["chicken_breast_raw", "olive_oil", "rice_white_raw"]
    rice = rows[2]
    assert rice["kcal_per_100g"] == "360"
    assert rice["protein_g_per_100g"] == "6.5"
    assert rice["carbs_g_per_100g"] == "79.3"
    assert rice["fat_g_per_100g"] == "0.58"
    assert rows[0]["diet_tags"] == "poultry|meat"
    assert rows[1]["g_per_ml"] == "0.92"
    assert rows[1]["conversion_note"] == "1 tbsp = 13.5 g"


def test_build_source_without_extract_row_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _build(tmp_path, source=_source_with_ghost(tmp_path))
    assert code != 0
    assert "123456" in capsys.readouterr().err
    assert not out.exists()


def test_build_is_deterministic(tmp_path: Path) -> None:
    _, first = _build(tmp_path, name="a.csv")
    _, second = _build(tmp_path, name="b.csv")
    assert first.read_bytes() == second.read_bytes()


def test_build_output_loads_as_catalog(tmp_path: Path) -> None:
    _, out = _build(tmp_path)
    assert load_catalog(out).get("olive_oil").g_per_ml == 0.92
