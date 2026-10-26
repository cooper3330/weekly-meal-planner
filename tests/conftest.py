"""Shared fixtures. The autouse guard keeps every test offline (ADR 0010)."""

import socket
from collections.abc import Iterator
from typing import Any

import pytest


class NetworkAccessBlocked(RuntimeError):
    """Raised when a test tries to open an internet connection."""


_INTERNET_FAMILIES = (socket.AF_INET, socket.AF_INET6)


def _blocked(address: Any) -> None:
    raise NetworkAccessBlocked(
        f"Tests must not use the network (tried to connect to {address!r}). "
        "Mock the LLM client and other external calls."
    )


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> Any:
        if self.family in _INTERNET_FAMILIES:
            _blocked(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if self.family in _INTERNET_FAMILIES:
            _blocked(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
