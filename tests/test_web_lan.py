import socket

import pytest

from cardak.web import lan


class FakeSocket:
    def __init__(self, address: str | None) -> None:
        self.address = address

    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def connect(self, target: tuple[str, int]) -> None:
        if self.address is None:
            raise OSError("Network is unreachable")

    def getsockname(self) -> tuple[str, int]:
        assert self.address is not None
        return self.address, 40000


@pytest.mark.parametrize("address", [None, "127.0.0.1"])
def test_lan_address_none_without_a_network(monkeypatch, address) -> None:
    monkeypatch.setattr(socket, "socket", lambda *a: FakeSocket(address))
    assert lan.lan_address() is None


def test_lan_address(monkeypatch) -> None:
    monkeypatch.setattr(socket, "socket", lambda *a: FakeSocket("192.168.1.20"))
    assert lan.lan_address() == "192.168.1.20"
