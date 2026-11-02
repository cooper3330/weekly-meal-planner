"""The fixed exclusion vocabulary (ADR 0016).

Core module: imports nothing from ``profile/`` or ``web/``.
"""

from enum import StrEnum
from typing import Literal

Kind = Literal["allergen", "diet"]


class ExclusionTerm(StrEnum):
    """One closed term a user can exclude; the ingredient catalog is tagged against these."""

    label: str
    definition: str
    kind: Kind

    def __new__(cls, value: str, label: str, definition: str, kind: Kind) -> "ExclusionTerm":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        obj.definition = definition
        obj.kind = kind
        return obj

    milk = (
        "milk",
        "Milk",
        "Milk and dairy products (butter, cheese, cream, yogurt, whey, casein)",
        "allergen",
    )
    egg = (
        "egg",
        "Eggs",
        "Eggs, whites and yolks, and anything made with them (mayonnaise, many pastas and "
        "baked goods)",
        "allergen",
    )
    fish = (
        "fish",
        "Fish",
        "Finned fish such as cod, salmon, bass, tuna and anchovy, and fish sauce",
        "allergen",
    )
    crustacean_shellfish = (
        "crustacean_shellfish",
        "Crustacean shellfish",
        "Shrimp, crab, lobster, crayfish",
        "allergen",
    )
    tree_nuts = (
        "tree_nuts",
        "Tree nuts",
        "Walnuts, almonds, cashews, pecans, pistachios, hazelnuts, macadamias, Brazil nuts, "
        "and their butters, oils and flours",
        "allergen",
    )
    peanuts = (
        "peanuts",
        "Peanuts",
        "Peanuts and peanut products (peanut butter, peanut oil)",
        "allergen",
    )
    wheat = (
        "wheat",
        "Wheat",
        "Wheat in any form (flour, semolina, spelt, couscous, most breads and pastas, "
        "soy sauce brewed with wheat)",
        "allergen",
    )
    soy = (
        "soy",
        "Soybeans",
        "Soybeans and soy products (tofu, edamame, soy sauce, miso, tempeh)",
        "allergen",
    )
    sesame = (
        "sesame",
        "Sesame",
        "Sesame seeds, sesame oil, tahini",
        "allergen",
    )
    meat = (
        "meat",
        "Meat",
        "Meat or poultry from any land animal, and anything made from them (stock, lard, gelatin)",
        "diet",
    )
    pork = (
        "pork",
        "Pork",
        "Pork and pork products (bacon, ham, lard)",
        "diet",
    )
    beef = (
        "beef",
        "Beef",
        "Beef and beef products (beef stock, beef gelatin)",
        "diet",
    )
    poultry = (
        "poultry",
        "Poultry",
        "Chicken, turkey, duck, and their products (chicken stock)",
        "diet",
    )
    mollusc_shellfish = (
        "mollusc_shellfish",
        "Mollusc shellfish",
        "Clams, mussels, oysters, scallops, squid, octopus",
        "diet",
    )
    gluten = (
        "gluten",
        "Gluten",
        "Wheat, barley, rye, and anything containing them",
        "diet",
    )
    alcohol = (
        "alcohol",
        "Alcohol",
        "Wine, beer, spirits, and cooking alcohol such as mirin",
        "diet",
    )


ALLERGENS: tuple[ExclusionTerm, ...] = tuple(t for t in ExclusionTerm if t.kind == "allergen")
DIET_TERMS: tuple[ExclusionTerm, ...] = tuple(t for t in ExclusionTerm if t.kind == "diet")
