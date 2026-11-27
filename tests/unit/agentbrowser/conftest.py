"""Runner tests that are about flows must not walk the repository's real feature map (#154).

`runner.MAP_PATH` defaults to tests/e2e/feature-map.json. Here it points at an empty map, so the
flow tests keep seeing "no flows" and exact app events; the walk's own tests pass `map_path=`.
"""

import json
from pathlib import Path

import pytest

from agentbrowser import runner


@pytest.fixture(autouse=True)
def _empty_feature_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    path: Path = tmp_path_factory.mktemp("empty-map") / "feature-map.json"
    path.write_text(json.dumps({"internal": [], "features": []}), encoding="utf-8")
    monkeypatch.setattr(runner, "MAP_PATH", path)
