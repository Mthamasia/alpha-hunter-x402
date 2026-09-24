import socket

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# Mints reais de exemplo (somente como strings; nenhuma consulta on-chain é feita).
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class NetworkBlocked(RuntimeError):
    pass


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(address) -> bool:
    host = address[0] if isinstance(address, tuple) else address
    return isinstance(host, str) and host in _LOOPBACK


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Qualquer conexão para fora de loopback falha o teste.

    Loopback é permitido porque o asyncio no Windows usa um socketpair interno
    (127.0.0.1) para o event loop do TestClient.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        if not _is_loopback(address):
            raise NetworkBlocked(f"network access is forbidden in tests: {address!r}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _is_loopback(address):
            raise NetworkBlocked(f"network access is forbidden in tests: {address!r}")
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in _LOOPBACK and host is not None:
            raise NetworkBlocked(f"DNS lookup is forbidden in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        raise NetworkBlocked(f"network access is forbidden in tests: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)


def make_client(**overrides) -> TestClient:
    return TestClient(create_app(Settings(**overrides)), raise_server_exceptions=False)


@pytest.fixture
def client():
    return make_client(payment_mode="disabled")


@pytest.fixture
def paid_client():
    return make_client(payment_mode="test", x402_network="testnet")
