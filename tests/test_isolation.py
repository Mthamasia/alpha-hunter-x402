"""Garante que a suíte não depende de rede nem do projeto alpha-hunter."""

import re
import socket
import sys
from pathlib import Path

import pytest

from tests.conftest import NetworkBlocked

ROOT = Path(__file__).resolve().parent.parent


def test_network_is_blocked_during_tests():
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("example.com", 80), timeout=1)
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("example.com", 443)
    s = socket.socket()
    try:
        with pytest.raises(NetworkBlocked):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()


def test_full_request_flow_without_network(client):
    # Se o fluxo tentasse rede, o guard do conftest levantaria NetworkBlocked -> 500.
    r = client.post("/v1/token/investigate",
                    json={"chain": "solana", "mint": "So11111111111111111111111111111111111111112"})
    assert r.status_code == 200


def test_no_alpha_hunter_dependency():
    local_bridge_modules = {
        "app.providers.alpha_hunter",
        "tests.test_alpha_hunter_provider",
    }
    assert not any(
        ("alpha_hunter" in module or "alphahunter" in module)
        and module not in local_bridge_modules
        for module in sys.modules
    )
    pattern = re.compile(
        r"(^\s*(?:from|import)\s+alpha(?:_hunter)?\b|alpha[-_]hunter[/\\])",
        re.I | re.M,
    )
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        assert not pattern.search(path.read_text(encoding="utf-8")), path
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        assert "alpha" not in line.lower()
