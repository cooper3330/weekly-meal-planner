"""Every flow in the repository's tests/flows/ loads under the strict rules (#144), no browser."""

from pathlib import Path

import pytest

from agentbrowser import flow, runner

PATHS = sorted(runner.FLOWS_DIR.glob("*.json"))


def test_the_flows_directory_has_flows() -> None:
    assert PATHS


@pytest.mark.parametrize("path", PATHS, ids=lambda p: Path(p).name)
def test_flow_loads_strictly_and_declares_what_it_proves(path: Path) -> None:
    loaded = flow.load(path, strict=True)
    assert loaded.proves
