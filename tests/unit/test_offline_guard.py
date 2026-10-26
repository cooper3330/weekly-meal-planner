import os
import socket

import pytest

from tests.conftest import NetworkAccessBlocked


def test_guard_blocks_ipv4_connect() -> None:
    with pytest.raises(NetworkAccessBlocked):
        socket.create_connection(("1.1.1.1", 443), timeout=1)


def test_guard_blocks_ipv6_connect() -> None:
    with pytest.raises(NetworkAccessBlocked):
        socket.create_connection(("2606:4700:4700::1111", 443), timeout=1)


def test_guard_blocks_connect_ex() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(NetworkAccessBlocked):
        s.connect_ex(("1.1.1.1", 443))


def test_guard_allows_local_socketpair() -> None:
    a, b = socket.socketpair()
    with a, b:
        a.sendall(b"ping")
        assert b.recv(4) == b"ping"


def test_api_key_absent_in_tests() -> None:
    assert "ANTHROPIC_API_KEY" not in os.environ
