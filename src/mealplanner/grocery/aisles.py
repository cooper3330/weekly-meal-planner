"""Grocery aisles (ADR 0023). The enum order is the order the list is printed in."""

from enum import StrEnum


class Aisle(StrEnum):
    produce = "produce"
    bakery = "bakery"
    meat_seafood = "meat_seafood"
    dairy_eggs = "dairy_eggs"
    pasta_rice_grains = "pasta_rice_grains"
    canned_jarred = "canned_jarred"
    baking_spices = "baking_spices"
    oils_condiments_sauces = "oils_condiments_sauces"
    international = "international"
    snacks_nuts_dried_fruit = "snacks_nuts_dried_fruit"
    beverages = "beverages"
    frozen = "frozen"
    not_purchased = "not_purchased"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS: dict[Aisle, str] = {
    Aisle.produce: "Produce",
    Aisle.bakery: "Bakery and bread",
    Aisle.meat_seafood: "Meat and seafood",
    Aisle.dairy_eggs: "Dairy and eggs",
    Aisle.pasta_rice_grains: "Pasta, rice and grains",
    Aisle.canned_jarred: "Canned and jarred",
    Aisle.baking_spices: "Baking and spices",
    Aisle.oils_condiments_sauces: "Oils, condiments and sauces",
    Aisle.international: "International",
    Aisle.snacks_nuts_dried_fruit: "Snacks, nuts and dried fruit",
    Aisle.beverages: "Beverages",
    Aisle.frozen: "Frozen",
    Aisle.not_purchased: "Not bought (tap water)",
}
