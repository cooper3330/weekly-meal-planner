"""Build the consolidated grocery list from checked meals (ADR 0024). Pure: no I/O, no model.

Quantities come only from the meals' structured ingredient lines (FR-7).
"""

import math
from collections.abc import Sequence

from mealplanner.grocery.aisles import Aisle
from mealplanner.grocery.info import GroceryInfo
from mealplanner.grocery.models import (
    AisleSection,
    Dimension,
    GroceryItem,
    GroceryList,
    NeedLine,
)
from mealplanner.nutrition.catalog import Catalog
from mealplanner.nutrition.units import (
    G_PER_KG,
    G_PER_LB,
    G_PER_OZ,
    ML_PER_CUP,
    ML_PER_L,
    ML_PER_TBSP,
    ML_PER_TSP,
    Unit,
    to_grams,
)
from mealplanner.planning.models import MealOption

# Base-unit factors: grams for mass, millilitres for volume, pieces for count.
_BASE: dict[Unit, tuple[Dimension, float]] = {
    Unit.g: (Dimension.mass, 1.0),
    Unit.kg: (Dimension.mass, G_PER_KG),
    Unit.oz: (Dimension.mass, G_PER_OZ),
    Unit.lb: (Dimension.mass, G_PER_LB),
    Unit.ml: (Dimension.volume, 1.0),
    Unit.l: (Dimension.volume, ML_PER_L),
    Unit.tsp: (Dimension.volume, ML_PER_TSP),
    Unit.tbsp: (Dimension.volume, ML_PER_TBSP),
    Unit.cup: (Dimension.volume, ML_PER_CUP),
    Unit.each: (Dimension.count, 1.0),
}
_PACK_TOLERANCE = 1e-9


def build_grocery_list(
    meals: Sequence[MealOption],
    *,
    catalog: Catalog,
    grocery_info: GroceryInfo,
    servings: int,
) -> GroceryList:
    if servings < 1:
        raise ValueError(f"servings must be at least 1, got {servings}")

    needs: dict[str, dict[Dimension, float]] = {}
    needed_g: dict[str, float] = {}
    used_in: dict[str, list[str]] = {}
    for meal in meals:
        factor = servings / meal.servings
        for line in meal.draft.ingredients:
            id = line.ingredient_id
            row = catalog.get(id)
            quantity = line.quantity * factor
            dimension, base = _BASE[line.unit]
            by_dim = needs.setdefault(id, {})
            by_dim[dimension] = by_dim.get(dimension, 0.0) + quantity * base
            needed_g[id] = needed_g.get(id, 0.0) + to_grams(quantity, line.unit, row)
            names = used_in.setdefault(id, [])
            if meal.draft.name not in names:
                names.append(meal.draft.name)

    by_aisle: dict[Aisle, list[GroceryItem]] = {}
    for id, by_dim in needs.items():
        info = grocery_info.get(id)
        buy_count: int | None = None
        if info.pack_quantity is not None and info.pack_unit is not None:
            pack_g = to_grams(info.pack_quantity, info.pack_unit, catalog.get(id))
            buy_count = max(1, math.ceil(needed_g[id] / pack_g - _PACK_TOLERANCE))
        item = GroceryItem(
            ingredient_id=id,
            name=catalog.get(id).name,
            aisle=info.aisle,
            needs=tuple(NeedLine(d, by_dim[d]) for d in Dimension if d in by_dim),
            buy_count=buy_count,
            pack_label=info.pack_label,
            meals=tuple(used_in[id]),
        )
        by_aisle.setdefault(info.aisle, []).append(item)

    sections = tuple(
        AisleSection(
            aisle,
            tuple(sorted(by_aisle[aisle], key=lambda i: (i.name.casefold(), i.ingredient_id))),
        )
        for aisle in Aisle
        if aisle in by_aisle
    )
    return GroceryList(sections)
