import mealplanner


def test_package_importable() -> None:
    assert isinstance(mealplanner.__version__, str)
    assert mealplanner.__version__
