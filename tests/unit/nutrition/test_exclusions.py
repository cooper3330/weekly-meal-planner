from mealplanner.nutrition.exclusions import ALLERGENS, DIET_TERMS, ExclusionTerm

EXPECTED = {
    "milk", "egg", "fish", "crustacean_shellfish", "tree_nuts", "peanuts", "wheat", "soy",
    "sesame", "meat", "pork", "beef", "poultry", "mollusc_shellfish", "gluten", "alcohol",
}  # fmt: skip


def test_members_are_exactly_the_sixteen() -> None:
    assert {t.value for t in ExclusionTerm} == EXPECTED
    assert len(ExclusionTerm) == 16


def test_every_member_has_label_definition_kind() -> None:
    for term in ExclusionTerm:
        assert term.label.strip()
        assert term.definition.strip()
        assert term.kind in ("allergen", "diet")


def test_allergens_and_diet_terms_partition_the_enum() -> None:
    assert len(ALLERGENS) == 9
    assert len(DIET_TERMS) == 7
    assert set(ALLERGENS).isdisjoint(DIET_TERMS)
    assert set(ALLERGENS) | set(DIET_TERMS) == set(ExclusionTerm)
    assert all(t.kind == "allergen" for t in ALLERGENS)
    assert all(t.kind == "diet" for t in DIET_TERMS)
    assert isinstance(ALLERGENS, tuple)
    assert isinstance(DIET_TERMS, tuple)
