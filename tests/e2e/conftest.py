from collections.abc import Iterator

import pytest
from fixture_server import Server


@pytest.fixture
def outside() -> Iterator[Server]:
    server = Server()
    yield server
    server.close()


@pytest.fixture
def site(outside: Server) -> Iterator[Server]:
    server = Server(external=outside.url)
    yield server
    server.close()
