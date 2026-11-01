"""Domain checks on schema-valid drafts (ADR 0018)."""

import inspect
from pathlib import Path

import pytest

from mealplanner.nutrition.catalog import load_catalog
from mealplanner.nutrition.compute import compute_nutrition
from mealplanner.nutrition.exclusions import ExclusionTerm
from mealplanner.nutrition.units import Unit
from mealplanner.planning.checks import check_option, meal_key, same_meal
from mealplanner.planning.models import MealOption, Rejection
from mealplanner.planning.schema import MealOptionDraft

CAT = load_catalog(Path(__file__).resolve().parents[2] / "fixtures" / "catalog" / "planning.csv")


def draft(
    *lines: tuple[str, float, str], name: str = "Test meal", cook_minutes: int = 30
) -> MealOptionDraft:
    return MealOptionDraft.model_validate(
        {
            "name": name,
            "description": "A meal for tests.",
            "cook_minutes": cook_minutes,
            "tags": [],
            "ingredients": [{"ingredient_id": i, "quantity": q, "unit": u} for i, q, u in lines],
        }
    )


def check(
    d: MealOptionDraft,
    *,
    servings: int = 1,
    max_cook_minutes: int = 60,
    exclusions: frozenset[ExclusionTerm] = frozenset(),
) -> MealOption | Rejection:
    return check_option(
        d,
        catalog=CAT,
        servings=servings,
        max_cook_minutes=max_cook_minutes,
        exclusions=exclusions,
    )


def test_valid_option_nutrition_exact() -> None:
    # 2 servings. 300 g chicken: 495 kcal, 93 p, 0 c, 10.8 f.
    # 200 g rice: 260 kcal, 6 p, 56 c, 0.6 f. Sum 755 kcal / 2 = 377.5 kcal per serving,
    # protein 99 / 2 = 49.5, carbs 56 / 2 = 28, fat 11.4 / 2 = 5.7.
    d = draft(("chicken", 300, "g"), ("rice", 200, "g"))
    result = check(d, servings=2)
    assert isinstance(result, MealOption)
    assert result.draft == d
    assert result.servings == 2
    assert result.nutrition == compute_nutrition(
        [("chicken", 300, Unit.g), ("rice", 200, Unit.g)], CAT, 2
    )
    assert result.nutrition.kcal == pytest.approx(377.5)
    assert result.nutrition.protein_g == pytest.approx(49.5)
    assert result.nutrition.carbs_g == pytest.approx(28.0)
    assert result.nutrition.fat_g == pytest.approx(5.7)


def test_option_and_rejection_are_frozen() -> None:
    result = check(draft(("rice", 200, "g")))
    assert isinstance(result, MealOption)
    with pytest.raises(AttributeError):
        result.servings = 3  # type: ignore[misc]
    rejection = check(draft(("rice", 200, "g"), cook_minutes=90))
    assert isinstance(rejection, Rejection)
    with pytest.raises(AttributeError):
        rejection.reason = "x"  # type: ignore[misc]


def test_excluded_ingredient_by_allergen() -> None:
    d = draft(("peanut_butter", 50, "g"), ("rice", 200, "g"), name="Peanut rice")
    result = check(d, exclusions=frozenset({ExclusionTerm.peanuts}))
    assert isinstance(result, Rejection)
    assert result.name == "Peanut rice"
    assert "peanuts" in result.reason
    assert "peanut_butter" in result.reason


@pytest.mark.parametrize("term", [ExclusionTerm.wheat, ExclusionTerm.gluten, ExclusionTerm.soy])
def test_composite_allergen(term: ExclusionTerm) -> None:
    result = check(
        draft(("rice", 200, "g"), ("soy_sauce", 2, "tbsp")), exclusions=frozenset({term})
    )
    assert isinstance(result, Rejection)
    assert term.value in result.reason
    assert "soy_sauce" in result.reason


def test_diet_tag_exclusion() -> None:
    d = draft(("rice", 200, "g"), ("chicken_stock", 300, "ml"))
    result = check(d, exclusions=frozenset({ExclusionTerm.meat}))
    assert isinstance(result, Rejection)
    assert "meat" in result.reason
    assert "chicken_stock" in result.reason


def test_unrelated_exclusion_passes() -> None:
    d = draft(("rice", 200, "g"), ("chicken", 200, "g"))
    assert isinstance(check(d, exclusions=frozenset({ExclusionTerm.peanuts})), MealOption)


def test_no_exclusions_passes() -> None:
    for d in (
        draft(("peanut_butter", 50, "g"), ("rice", 200, "g")),
        draft(("rice", 200, "g"), ("soy_sauce", 2, "tbsp")),
        draft(("rice", 200, "g"), ("chicken_stock", 300, "ml")),
    ):
        assert isinstance(check(d), MealOption)


def test_exclusions_checked_before_cook_time() -> None:
    d = draft(("peanut_butter", 50, "g"), ("rice", 200, "g"), cook_minutes=200)
    result = check(d, exclusions=frozenset({ExclusionTerm.peanuts}))
    assert isinstance(result, Rejection)
    assert "peanuts" in result.reason


def test_cook_time_over_max_rejected() -> None:
    over = check(draft(("rice", 200, "g"), cook_minutes=46), max_cook_minutes=45)
    assert isinstance(over, Rejection)
    assert "46" in over.reason
    assert "45" in over.reason
    assert isinstance(
        check(draft(("rice", 200, "g"), cook_minutes=45), max_cook_minutes=45), MealOption
    )


def test_quantity_sanity() -> None:
    # rice is 130 kcal per 100 g, so these cases stay inside the kcal range.
    assert isinstance(check(draft(("rice", 1000, "g"))), MealOption)
    rejected = check(draft(("rice", 1000.1, "g")))
    assert isinstance(rejected, Rejection)
    assert "rice" in rejected.reason
    assert isinstance(check(draft(("rice", 4, "kg")), servings=4), MealOption)
    assert isinstance(check(draft(("rice", 4, "kg")), servings=2), Rejection)


def test_quantity_checked_before_nutrition() -> None:
    result = check(draft(("olive_oil", 1500, "g")))
    assert isinstance(result, Rejection)
    assert "1000" in result.reason


@pytest.mark.parametrize(
    ("grams", "ok"),
    [(149.9, False), (150.0, True)],
)
def test_kcal_plausibility_fuel(grams: float, ok: bool) -> None:
    # fuel is 100 kcal per 100 g, so grams equal kcal for one serving.
    assert isinstance(check(draft(("fuel", grams, "g"))), MealOption) is ok


@pytest.mark.parametrize(
    ("grams", "ok"),
    [(500.0, True), (500.02, False), (400.02, True)],
)
def test_kcal_plausibility_dense(grams: float, ok: bool) -> None:
    # dense is 500 kcal per 100 g: 500 g is exactly 2500 kcal, 500.02 g is 2500.1 kcal.
    result = check(draft(("dense", grams, "g")))
    assert isinstance(result, MealOption) is ok
    if not ok:
        assert isinstance(result, Rejection)
        assert "kcal" in result.reason


def test_kcal_low_rejection_names_value() -> None:
    result = check(draft(("fuel", 149.9, "g")))
    assert isinstance(result, Rejection)
    assert "149.9" in result.reason


def test_unconvertible_unit_is_a_rejection_not_a_crash() -> None:
    # A draft built without the catalog-bound schema can carry a unit with no conversion.
    result = check(draft(("rice", 1, "cup")))
    assert isinstance(result, Rejection)
    assert "rice" in result.reason


def test_unknown_ingredient_is_a_rejection_not_a_crash() -> None:
    result = check(draft(("gochujang", 1, "g")))
    assert isinstance(result, Rejection)
    assert "gochujang" in result.reason


def test_meal_key() -> None:
    keys = {
        meal_key("Chicken Stir-Fry"),
        meal_key(" chicken  stir fry "),
        meal_key("CHICKEN STIR—FRY"),
    }
    assert keys == {"chicken stir fry"}
    assert meal_key("Chicken Stir-Fry 2") != meal_key("Chicken Stir-Fry")
    assert meal_key("Ｃｈｉｃｋｅｎ") == "chicken"  # NFKC folds full-width letters
    assert meal_key("Straße") == meal_key("STRASSE")  # casefold
    assert meal_key("---") == ""


def test_same_meal_by_name() -> None:
    a = draft(("rice", 100, "g"), name="Chicken Stir-Fry")
    b = draft(("egg", 2, "each"), name="chicken stir fry")
    assert same_meal(a, b)


def test_same_meal_by_ingredients() -> None:
    a = draft(("rice", 100, "g"), ("chicken", 300, "g"), ("egg", 1, "each"), name="Alpha bowl")
    b = draft(("egg", 3, "each"), ("chicken", 50, "g"), ("rice", 400, "g"), name="Beta plate")
    assert same_meal(a, b)


def test_different_meals() -> None:
    a = draft(("rice", 100, "g"), ("chicken", 300, "g"), name="Alpha bowl")
    b = draft(("rice", 100, "g"), ("egg", 2, "each"), name="Beta plate")
    c = draft(("rice", 100, "g"), ("chicken", 300, "g"), ("egg", 1, "each"), name="Gamma")
    assert not same_meal(a, b)
    assert not same_meal(a, c)  # a subset is not the same meal


def test_same_meal_with_stored_recent_meal() -> None:
    from mealplanner.planning.models import RecentMeal

    d = draft(("rice", 100, "g"), ("chicken", 300, "g"), name="Alpha bowl")
    by_name = RecentMeal(name="ALPHA  bowl", ingredient_ids=frozenset({"egg"}))
    by_ids = RecentMeal(name="Other", ingredient_ids=frozenset({"chicken", "rice"}))
    other = RecentMeal(name="Other", ingredient_ids=frozenset({"egg"}))
    assert same_meal(d, by_name)
    assert same_meal(by_ids, d)
    assert same_meal(by_name, d)
    assert not same_meal(d, other)
    assert same_meal(by_name, RecentMeal(name="alpha bowl", ingredient_ids=frozenset()))


def test_no_target_inputs() -> None:
    names = set(inspect.signature(check_option).parameters)
    assert names == {"draft", "catalog", "servings", "max_cook_minutes", "exclusions"}
    for name in names:
        for word in ("calorie", "kcal", "macro", "protein", "carb", "fat", "target"):
            assert word not in name


def test_planning_has_no_web_imports() -> None:
    import subprocess
    import sys

    src = str(Path(__file__).resolve().parents[3] / "src")
    code = (
        f"import sys; sys.path.insert(0, {src!r});"
        "import mealplanner.planning.schema, mealplanner.planning.checks, "
        "mealplanner.planning.models;"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('fastapi', 'jinja2', 'starlette') "
        "or m.startswith('mealplanner.web')];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
