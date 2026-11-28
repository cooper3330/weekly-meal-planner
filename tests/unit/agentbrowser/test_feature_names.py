"""Feature names match the publisher's pattern (#173, ADR 0030 change 5). No browser."""

import json
from pathlib import Path

import pytest
from test_feature_map_model import HOME, build, feature

from agentbrowser import feature_map as fm


def _load_named(tmp_path: Path, name: str) -> fm.FeatureMap:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(build(feature(name, HOME, routes=["GET /x"]))))
    return fm.load(path)


@pytest.mark.parametrize("name", ["profile-version", "a", "a" * 64, "0-9"])
def test_names_that_match_load(tmp_path: Path, name: str) -> None:
    assert [f.name for f in _load_named(tmp_path, name).features] == [name]


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("profile_version", "profile_version"),
        ("Profile", "Profile"),
        ("-profile", "-profile"),
        ("a" * 65, "a" * 65),
        ("", "<entry 0>"),
    ],
)
def test_names_that_do_not_match_fail_naming_the_entry(
    tmp_path: Path, name: str, shown: str
) -> None:
    with pytest.raises(fm.FeatureMapError) as info:
        _load_named(tmp_path, name)
    assert any(line.startswith(f"{shown}: name:") for line in info.value.errors)
    assert "^[a-z0-9][a-z0-9-]{0,63}$" in str(info.value)
