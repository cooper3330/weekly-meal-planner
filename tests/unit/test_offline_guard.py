import os
import socket
import subprocess
import sys

import pytest

BLOCKED = "Tests must not use the network"


def test_guard_blocks_ipv4_connect() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.create_connection(("1.1.1.1", 443), timeout=1)


def test_guard_blocks_ipv6_connect() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.create_connection(("2606:4700:4700::1111", 443), timeout=1)


def test_guard_blocks_connect_ex() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s,
        pytest.raises(RuntimeError, match=BLOCKED),
    ):
        s.connect_ex(("1.1.1.1", 443))


def test_guard_blocks_hostname_lookup() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED):
        socket.getaddrinfo("api.anthropic.com", 443)


def test_guard_allows_numeric_and_local_lookup() -> None:
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("localhost", 80)


def test_guard_allows_local_socketpair() -> None:
    a, b = socket.socketpair()
    with a, b:
        a.sendall(b"ping")
        assert b.recv(4) == b"ping"


def test_api_key_absent_in_tests() -> None:
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_api_key_removed_even_when_outer_shell_sets_it() -> None:
    env = {**os.environ, "ANTHROPIC_API_KEY": "x"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"{__file__}::test_api_key_absent_in_tests",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
