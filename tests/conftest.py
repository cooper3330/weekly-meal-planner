"""Shared fixtures. The autouse guard keeps every test offline (ADR 0010)."""

import ipaddress
import socket
from collections.abc import Iterator
from typing import Any

import pytest

_INTERNET_FAMILIES = (socket.AF_INET, socket.AF_INET6)
_LOCAL_NAMES = {"localhost", "ip6-localhost"}


def _blocked(target: Any) -> None:
    raise RuntimeError(
        f"Tests must not use the network (tried to reach {target!r}). "
        "Mock the LLM client and other external calls."
    )


def _is_local_host(host: Any) -> bool:
    if host is None or host == "":
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if str(host).lower() in _LOCAL_NAMES:
        return True
    try:
        ipaddress.ip_address(str(host).split("%")[0])
    except ValueError:
        return False  # a real hostname: would need DNS
    return True


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    # connect is patched on the socket class, so asyncio and httpx paths are covered too.
    def connect(self: socket.socket, address: Any) -> Any:
        if self.family in _INTERNET_FAMILIES:
            _blocked(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if self.family in _INTERNET_FAMILIES:
            _blocked(address)
        return real_connect_ex(self, address)

    # Hostname lookups would reach DNS before connect; numeric/localhost lookups stay allowed.
    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_local_host(host):
            _blocked(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
