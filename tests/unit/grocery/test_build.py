import random
from pathlib import Path

import pytest
from grocery_support import CAT, INFO, make_meal

from mealplanner.grocery.aisles import Aisle
from mealplanner.grocery.build import build_grocery_list
from mealplanner.grocery.info import load_grocery_info
from mealplanner.grocery.models import Dimension, GroceryItem, GroceryList
from mealplanner.nutrition.catalog import load_catalog
from mealplanner.nutrition.units import G_PER_LB, ML_PER_CUP, ML_PER_TBSP
from mealplanner.planning.checks import check_option
from mealplanner.planning.models import Rejection
from mealplanner.planning.schema import MealOptionDraft


def build(meals, servings: int = 2) -> GroceryList:
    return build_grocery_list(meals, catalog=CAT, grocery_info=INFO, servings=servings)


def items(glist: GroceryList) -> dict[str, GroceryItem]:
    return {i.ingredient_id: i for s in glist.sections for i in s.items}


def amounts(item: GroceryItem) -> dict[Dimension, float]:
    return {n.dimension: n.amount for n in item.needs}


def test_every_ingredient_present() -> None:
    meals = [
        make_meal([("chicken", 300, "g"), ("rice", 100, "g")], name="Meal one"),
        make_meal([("egg", 2, "each"), ("rice", 50, "g")], name="Meal two"),
        make_meal([("olive_oil", 1, "tbsp"), ("soy_sauce", 1, "tsp")], name="Meal three"),
    ]
    expected = {line.ingredient_id for m in meals for line in m.draft.ingredients}
    assert set(items(build(meals))) == expected


def test_same_unit_summed() -> None:
    meals = [
        make_meal([("rice", 200, "g")], name="Meal one"),
        make_meal([("rice", 300, "g")], name="Meal two"),
    ]
    rice = items(build(meals))["rice"]
    assert [(n.dimension, n.amount) for n in rice.needs] == [(Dimension.mass, 500)]


def test_compatible_units_summed() -> None:
    meal = make_meal(
        [("rice", 1, "lb"), ("rice", 100, "g"), ("olive_oil", 1, "cup"), ("olive_oil", 2, "tbsp")]
    )
    found = items(build([meal]))
    assert amounts(found["rice"]) == {Dimension.mass: pytest.approx(553.59237)}
    assert amounts(found["olive_oil"]) == {
        Dimension.volume: pytest.approx(ML_PER_CUP + 2 * ML_PER_TBSP)
    }


def test_incompatible_units_separate() -> None:
    meal = make_meal([("egg", 2, "each"), ("egg", 100, "g")])
    egg = items(build([meal]))["egg"]
    assert [(n.dimension, n.amount) for n in egg.needs] == [
        (Dimension.mass, 100),
        (Dimension.count, 2),
    ]


def test_same_ingredient_twice_in_one_meal() -> None:
    meal = make_meal([("rice", 100, "g"), ("rice", 50, "g")], name="Rice twice")
    rice = items(build([meal]))["rice"]
    assert amounts(rice) == {Dimension.mass: 150}
    assert rice.meals == ("Rice twice",)


def test_scaling_up_and_down() -> None:
    meal = make_meal([("rice", 100, "g"), ("egg", 2, "each")], servings=2)
    up = items(build([meal], servings=4))
    assert amounts(up["rice"]) == {Dimension.mass: 200}
    assert amounts(up["egg"]) == {Dimension.count: 4}
    down = items(build([meal], servings=1))
    assert amounts(down["rice"]) == {Dimension.mass: 50}
    assert amounts(down["egg"]) == {Dimension.count: 1}


def test_scaling_mixed_servings_each_by_own_factor() -> None:
    two = make_meal([("rice", 100, "g")], servings=2, name="For two")
    four = make_meal([("rice", 400, "g")], servings=4, name="For four")
    rice = items(build([two, four], servings=2))["rice"]
    assert amounts(rice) == {Dimension.mass: 100 + 200}


def test_scaling_unchanged() -> None:
    meal = make_meal([("rice", 1, "lb"), ("olive_oil", 2, "tbsp")], servings=3)
    found = items(build([meal], servings=3))
    assert amounts(found["rice"]) == {Dimension.mass: pytest.approx(G_PER_LB)}
    assert amounts(found["olive_oil"]) == {Dimension.volume: pytest.approx(2 * ML_PER_TBSP)}


def test_every_item_has_aisle() -> None:
    meal = make_meal(
        [("chicken", 200, "g"), ("rice", 100, "g"), ("egg", 2, "each"), ("soy_sauce", 1, "tbsp")]
    )
    glist = build([meal])
    aisles = [s.aisle for s in glist.sections]
    assert aisles == sorted(aisles, key=list(Aisle).index)
    assert len(set(aisles)) == len(aisles)
    for section in glist.sections:
        assert section.items
        for item in section.items:
            assert item.aisle is section.aisle is INFO.get(item.ingredient_id).aisle


def test_items_sorted_by_name_case_insensitive(tmp_path: Path) -> None:
    cat_path = tmp_path / "catalog.csv"
    cat_path.write_text(
        "id,name,aliases,fdc_id,kcal_per_100g,protein_g_per_100g,carbs_g_per_100g,"
        "fat_g_per_100g,g_per_ml,g_per_each,allergens,diet_tags\n"
        "a,apple,,1,100,0,0,0,,,,\n"
        "b,Banana,,2,100,0,0,0,,,,\n"
        "c,cherry,,3,100,0,0,0,,,,\n"
        "d,Date,,4,100,0,0,0,,,,\n",
        encoding="utf-8",
    )
    info_path = tmp_path / "grocery.csv"
    info_path.write_text(
        "id,aisle,pack_quantity,pack_unit,pack_label\n"
        + "".join(f"{i},produce,1,lb,1 lb bag\n" for i in "abcd"),
        encoding="utf-8",
    )
    cat = load_catalog(cat_path)
    info = load_grocery_info(cat, info_path)
    draft = MealOptionDraft.model_validate(
        {
            "name": "Fruit",
            "description": "Fruit.",
            "cook_minutes": 5,
            "tags": ["quick"],
            "ingredients": [
                {"ingredient_id": i, "quantity": 400, "unit": "g"} for i in ("d", "c", "b", "a")
            ],
        }
    )
    meal = check_option(draft, catalog=cat, servings=2, max_cook_minutes=60, exclusions=frozenset())
    assert not isinstance(meal, Rejection), meal
    glist = build_grocery_list([meal], catalog=cat, grocery_info=info, servings=2)
    assert [i.name for i in glist.sections[0].items] == ["apple", "Banana", "cherry", "Date"]


def test_meals_listed() -> None:
    meals = [
        make_meal([("rice", 100, "g"), ("egg", 1, "each")], name="Meal one"),
        make_meal([("rice", 100, "g")], name="Meal two"),
        make_meal([("rice", 100, "g")], name="Meal one"),
    ]
    found = items(build(meals))
    assert found["rice"].meals == ("Meal one", "Meal two")
    assert found["egg"].meals == ("Meal one",)


def test_buy_count_pinch() -> None:
    meal = make_meal([("soy_sauce", 0.125, "tsp")])
    soy = items(build([meal]))["soy_sauce"]
    assert soy.buy_count == 1
    assert soy.pack_label == "10 fl oz bottle"


def test_buy_count_exact_multiple() -> None:
    meal = make_meal([("rice", 1, "lb"), ("rice", 1, "lb"), ("rice", 1, "lb"), ("rice", 1, "lb")])
    assert items(build([meal]))["rice"].buy_count == 2  # 4 lb with a 2 lb bag
    meal = make_meal([("egg", 6, "each"), ("egg", 6, "each")])
    assert items(build([meal]))["egg"].buy_count == 1


def test_buy_count_exact_multiple_after_scaling() -> None:
    # 0.1 * 3 style float error must not tip an exact multiple over.
    meal = make_meal([("egg", 4, "each")], servings=2)
    assert items(build([meal], servings=6))["egg"].buy_count == 1  # 12 eggs, one dozen
    assert items(build([meal], servings=6 + 0))["egg"].buy_count == 1


def test_buy_count_rounds_up() -> None:
    meal = make_meal([("chicken", 1.3, "lb")])
    assert items(build([meal]))["chicken"].buy_count == 2


def test_buy_count_mixed_dimensions() -> None:
    meal = make_meal([("egg", 10, "each"), ("egg", 200, "g")])
    egg = items(build([meal]))["egg"]
    assert len(egg.needs) == 2
    assert egg.buy_count == 2  # 500 g + 200 g = 700 g of 600 g dozens


def test_pack_in_each_need_in_grams() -> None:
    meal = make_meal([("egg", 600, "g")])
    assert items(build([meal]))["egg"].buy_count == 1


def test_not_purchased_has_no_buy() -> None:
    meal = make_meal([("fuel", 100, "g")])
    glist = build([meal])
    fuel = items(glist)["fuel"]
    assert fuel.buy_count is None
    assert fuel.pack_label is None
    assert glist.sections[-1].aisle is Aisle.not_purchased
    assert fuel in glist.sections[-1].items


def test_empty_meals() -> None:
    assert build([]).sections == ()


@pytest.mark.parametrize("servings", [0, -1])
def test_invalid_servings(servings: int) -> None:
    with pytest.raises(ValueError):
        build([make_meal([("rice", 100, "g")])], servings=servings)


def test_deterministic() -> None:
    meals = [
        make_meal([("rice", 100, "g"), ("egg", 2, "each")], name="Meal one"),
        make_meal([("rice", 200, "g"), ("chicken", 1, "lb")], name="Meal two"),
        make_meal([("egg", 3, "each"), ("rice", 50, "g")], name="Meal three"),
    ]
    first = build(meals)
    assert build(meals) == first
    shuffled = list(meals)
    random.Random(1).shuffle(shuffled)
    other = items(build(shuffled))
    for id, item in items(first).items():
        assert other[id].aisle is item.aisle
        assert other[id].buy_count == item.buy_count
        assert [n.dimension for n in other[id].needs] == [n.dimension for n in item.needs]
        for a, b in zip(other[id].needs, item.needs, strict=True):
            assert a.amount == pytest.approx(b.amount)
        assert set(other[id].meals) == set(item.meals)
