from pathlib import Path

import pytest

from mealplanner.nutrition import catalog as catalog_module
from mealplanner.nutrition.catalog import Catalog, CatalogError, CatalogRow, load_catalog
from mealplanner.nutrition.exclusions import ExclusionTerm

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "catalog"


def test_load_valid_fixture() -> None:
    cat = load_catalog(FIXTURES / "valid.csv")
    assert len(cat.rows()) == 6
    assert cat.ids() == ("chicken", "egg", "milk", "olive_oil", "rice", "soy_sauce")
    assert "rice" in cat
    assert "pasta" not in cat
    assert cat.get("chicken") == CatalogRow(
        id="chicken",
        name="Chicken breast",
        aliases=("chicken breast", "poultry cut"),
        fdc_id=100001,
        kcal_per_100g=165.0,
        protein_g_per_100g=31.0,
        carbs_g_per_100g=0.0,
        fat_g_per_100g=3.6,
        g_per_ml=None,
        g_per_each=None,
        allergens=frozenset(),
        diet_tags=frozenset({ExclusionTerm.poultry, ExclusionTerm.meat}),
    )
    assert cat.get("milk").g_per_ml == 1.03
    assert cat.get("egg").g_per_each == 50.0
    assert cat.get("rice").aliases == ()


def test_composite_row_tags() -> None:
    soy = load_catalog(FIXTURES / "valid.csv").get("soy_sauce")
    assert soy.allergens == {ExclusionTerm.soy, ExclusionTerm.wheat}
    assert soy.diet_tags == {ExclusionTerm.gluten}


def test_unknown_id_raises_key_error() -> None:
    with pytest.raises(KeyError):
        load_catalog(FIXTURES / "valid.csv").get("pasta")


def test_catalog_is_hashable_and_immutable() -> None:
    a = load_catalog(FIXTURES / "valid.csv")
    b = load_catalog(FIXTURES / "valid.csv")
    assert isinstance(a, Catalog)
    assert hash(a) == hash(b)
    assert a == b


def test_load_default_path_uses_package_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_module, "_default_catalog_path", lambda: FIXTURES / "valid.csv")
    assert load_catalog().ids() == load_catalog(FIXTURES / "valid.csv").ids()


@pytest.mark.parametrize(
    ("filename", "needle"),
    [
        ("dup_id.csv", "line 3, column id"),
        ("dup_alias.csv", "line 3, column aliases"),
        ("bad_id_upper.csv", "line 2, column id"),
        ("bad_id_space.csv", "line 2, column id"),
        ("negative_kcal.csv", "line 2, column kcal_per_100g"),
        ("nan_protein.csv", "line 2, column protein_g_per_100g"),
        ("zero_density.csv", "line 2, column g_per_ml"),
        ("unknown_allergen.csv", "line 2, column allergens"),
        ("diet_in_allergens.csv", "line 2, column allergens"),
        ("allergen_in_diet.csv", "line 2, column diet_tags"),
        ("missing_column.csv", "column fat_g_per_100g"),
    ],
)
def test_invalid_rows_rejected(filename: str, needle: str) -> None:
    with pytest.raises(CatalogError, match=needle):
        load_catalog(FIXTURES / filename)


def test_extra_column_ignored() -> None:
    assert load_catalog(FIXTURES / "extra_column.csv") == load_catalog(FIXTURES / "valid.csv")
