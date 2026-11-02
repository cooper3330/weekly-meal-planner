"""Integrity checks on the committed ingredient catalog (ADR 0007, 0016, 0020)."""

import re
from pathlib import Path

import pytest

from mealplanner.nutrition import build_catalog
from mealplanner.nutrition.catalog import CatalogRow, load_catalog
from mealplanner.nutrition.exclusions import ExclusionTerm as T

DATA = Path(build_catalog.__file__).parent / "data"

ROWS: tuple[CatalogRow, ...] = load_catalog().rows()
BY_ID = {row.id: row for row in ROWS}


def _tags(row: CatalogRow) -> frozenset[T]:
    return row.allergens | row.diet_tags


def test_committed_catalog_is_reproducible(tmp_path: Path) -> None:
    out = tmp_path / "ingredients.csv"
    code = build_catalog.main(["build", "--out", str(out)])
    assert code == 0
    assert out.read_bytes() == (DATA / "ingredients.csv").read_bytes()


def test_committed_extract_covers_the_source() -> None:
    # Every fdc_id in the source is in the extract (build fails otherwise), and the extract
    # holds nothing else, so a removed source row cannot leave a stale extract row behind.
    source = {r.fdc_id for r in ROWS}
    extract_ids = {
        int(line.split(",", 1)[0])
        for line in (DATA / "fdc_extract.csv").read_text().splitlines()[1:]
    }
    assert extract_ids == source


def test_catalog_loads() -> None:
    assert len(ROWS) >= 450


def test_unique_ids_and_aliases() -> None:
    names = [row.id.lower() for row in ROWS]
    for row in ROWS:
        names.extend(alias.lower() for alias in row.aliases)
    assert len(names) == len(set(names))


def test_macros_at_most_100g() -> None:
    for row in ROWS:
        total = row.protein_g_per_100g + row.carbs_g_per_100g + row.fat_g_per_100g
        assert total <= 100.5, f"{row.id}: macros sum to {total}"


# Rows whose label energy legitimately differs from 4/4/9 (ADR 0007). Each needs a reason.
_FIBER = (
    "high dietary fiber: FDC carbohydrate by difference includes fiber that gives under 4 kcal/g"
)
_ALCOHOL = "contains ethanol, which gives 7 kcal/g and is not one of the three macros"
ATWATER_EXCEPTIONS: dict[str, str] = {
    **{
        id: _FIBER
        for id in (
            "allspice_ground",
            "anise_seed",
            "basil_dried",
            "bay_leaf",
            "caraway_seed",
            "cardamom_ground",
            "celery_seed",
            "chervil_dried",
            "chili_powder",
            "cinnamon_ground",
            "cloves_ground",
            "cocoa_powder_unsweetened",
            "coriander_leaf_dried",
            "coriander_seed",
            "cumin_ground",
            "curry_powder",
            "dill_seed",
            "dill_weed_dried",
            "fennel_seed",
            "fenugreek_seed",
            "marjoram_dried",
            "oat_bran_raw",
            "oregano_dried",
            "paprika",
            "parsley_dried",
            "pepper_black",
            "pepper_cayenne",
            "poultry_seasoning",
            "pumpkin_pie_spice",
            "rosemary_dried",
            "saffron",
            "sage_ground",
            "savory_ground",
            "tarragon_dried",
            "thyme_dried",
            "thyme_fresh",
            "tomato_sun_dried",
            "wheat_bran",
            "yeast_active_dry",
        )
    },
    **{
        id: _ALCOHOL
        for id in (
            "beer",
            "mirin",
            "spirits_80_proof",
            "vanilla_extract",
            "wine_cooking",
            "wine_red",
            "wine_white",
        )
    },
    "baking_powder": "mostly non-caloric leavening salts counted as carbohydrate by difference",
}


def test_atwater_agreement() -> None:
    for row in ROWS:
        if row.id in ATWATER_EXCEPTIONS:
            continue
        expected = 4 * row.protein_g_per_100g + 4 * row.carbs_g_per_100g + 9 * row.fat_g_per_100g
        diff = abs(row.kcal_per_100g - expected)
        assert diff <= max(0.15 * row.kcal_per_100g, 20), (
            f"{row.id}: {row.kcal_per_100g} kcal vs {expected:.0f} from macros"
        )


def test_atwater_exceptions_are_needed_and_explained() -> None:
    for id, reason in ATWATER_EXCEPTIONS.items():
        assert id in BY_ID, f"{id} is not in the catalog"
        assert reason.strip()
        row = BY_ID[id]
        expected = 4 * row.protein_g_per_100g + 4 * row.carbs_g_per_100g + 9 * row.fat_g_per_100g
        diff = abs(row.kcal_per_100g - expected)
        assert diff > max(0.15 * row.kcal_per_100g, 20), f"{id} no longer needs an exception"


def test_tag_implications() -> None:
    for row in ROWS:
        tags = _tags(row)
        if tags & {T.pork, T.beef, T.poultry}:
            assert T.meat in tags, f"{row.id}: pork/beef/poultry without meat"
        if T.wheat in tags:
            assert T.gluten in tags, f"{row.id}: wheat without gluten"


# id -> tags it must carry at least (every example in the task, plus a few safety-critical ones).
KNOWN_COMPOSITES: dict[str, set[T]] = {
    "soy_sauce": {T.soy, T.wheat, T.gluten},
    "worcestershire_sauce": {T.fish},
    "chicken_stock": {T.poultry, T.meat},
    "beef_stock": {T.beef, T.meat},
    "fish_sauce": {T.fish},
    "mirin": {T.alcohol},
    "pesto": {T.milk, T.tree_nuts},
    "mayonnaise": {T.egg, T.soy},
    "bread_white": {T.wheat, T.gluten},
    "pasta_dry": {T.wheat, T.gluten},
    "couscous_dry": {T.wheat, T.gluten},
    "barley_pearled_raw": {T.gluten},
    "rye_flour": {T.gluten},
    "egg_noodles_dry": {T.egg, T.wheat, T.gluten},
    "oyster_sauce": {T.mollusc_shellfish},
    "tofu_firm_raw": {T.soy},
    "miso": {T.soy},
    "tahini": {T.sesame},
    "peanut_butter": {T.peanuts},
    "butter_salted": {T.milk},
    "bacon_raw": {T.pork, T.meat},
    "wine_red": {T.alcohol},
    "beer": {T.alcohol, T.gluten},
    "pasta_fresh": {T.egg, T.wheat, T.gluten},
    # Soybean oil is tagged soy by default (ADR 0016, P9), with the products made from it.
    "oil_vegetable_soybean": {T.soy},
    "shortening_vegetable": {T.soy},
    "margarine_tub": {T.soy, T.milk},
    "dressing_ranch": {T.soy},
    "dressing_caesar": {T.soy},
}


def test_known_composites_tagged() -> None:
    for id, required in KNOWN_COMPOSITES.items():
        assert id in BY_ID, f"{id} is not in the catalog"
        assert required <= _tags(BY_ID[id]), f"{id} is missing {required - _tags(BY_ID[id])}"


def test_meat_coverage() -> None:
    prefixes = (
        "chicken_",
        "turkey_",
        "duck_",
        "beef_",
        "pork_",
        "bacon",
        "ham",
        "lamb_",
        "sausage",
    )
    matched = [row for row in ROWS if row.id.startswith(prefixes)]
    assert matched
    for row in matched:
        assert T.meat in row.diet_tags, f"{row.id} is not tagged meat"


def test_fish_coverage() -> None:
    matched = [
        row for row in ROWS if row.id.startswith(("salmon_", "tuna_", "cod_", "anchov", "sardine"))
    ]
    assert matched
    for row in matched:
        assert T.fish in row.allergens, f"{row.id} is not tagged fish"


def test_shellfish_coverage() -> None:
    crustacean = [row for row in ROWS if "shrimp" in row.id or "crab" in row.id]
    mollusc = [row for row in ROWS if any(w in row.id for w in ("clam", "mussel", "squid"))]
    assert crustacean
    assert mollusc
    for row in crustacean:
        assert T.crustacean_shellfish in row.allergens, f"{row.id} is not tagged crustacean"
    for row in mollusc:
        assert T.mollusc_shellfish in row.diet_tags, f"{row.id} is not tagged mollusc"


def test_conversions_positive() -> None:
    for row in ROWS:
        if row.g_per_ml is not None:
            assert row.g_per_ml > 0
        if row.g_per_each is not None:
            assert row.g_per_each > 0


LIQUID_WORDS = re.compile(r"\b(oil|milk|sauce|stock|broth|vinegar|juice|water)\b", re.IGNORECASE)


# "canned in oil" names the packing medium of a solid food (tuna, peaches), not a liquid row.
PACKING_MEDIUM = re.compile(r"\bin (oil|water|juice)\b", re.IGNORECASE)


def test_liquid_names_have_g_per_ml() -> None:
    for row in ROWS:
        if LIQUID_WORDS.search(PACKING_MEDIUM.sub("", row.name)):
            assert row.g_per_ml is not None, f"{row.id} ({row.name}) needs g_per_ml"


# Liquids as recipes measure them: a cup weighs roughly a cup of water.
LIQUID_DENSITY_WORDS = re.compile(
    r"\b(oil|milk|cream(?! of)|stock|broth|vinegar|juice|water)\b", re.I
)


def test_liquid_g_per_ml_is_plausible() -> None:
    for row in ROWS:
        name = PACKING_MEDIUM.sub("", row.name)
        # Cheeses ("whole milk") and dry products are not liquids despite the word.
        if row.name.startswith("Cheese") or re.search(r"\b(dry|dried|powder)\b", name, re.I):
            continue
        if row.g_per_ml is not None and LIQUID_DENSITY_WORDS.search(name):
            assert 0.8 <= row.g_per_ml <= 1.5, f"{row.id} g_per_ml {row.g_per_ml} is not liquid"


def test_heavy_cream_uses_the_fluid_cup() -> None:
    assert BY_ID["cream_heavy"].g_per_ml == pytest.approx(238 / 236.588, abs=0.001)


# Ids that may deliberately share one FDC food (none yet).
SHARED_FDC_IDS: set[frozenset[str]] = set()


def test_fdc_ids_unique_per_id() -> None:
    owners: dict[int, list[str]] = {}
    for row in ROWS:
        owners.setdefault(row.fdc_id, []).append(row.id)
    for fdc_id, ids in owners.items():
        if len(ids) > 1:
            assert frozenset(ids) in SHARED_FDC_IDS, f"fdc_id {fdc_id} is shared by {ids}"
