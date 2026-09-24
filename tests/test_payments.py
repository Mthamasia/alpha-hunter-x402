import base64
import json

import pytest

from app.config import ConfigError, Settings
from app.payments.gate import build_test_payment_header
from tests.conftest import WSOL_MINT, make_client

URL = "/v1/token/investigate"
REQ = {"chain": "solana", "mint": WSOL_MINT}


def _b64(obj) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def test_disabled_mode_allows_access(client):
    r = client.post(URL, json=REQ)
    assert r.status_code == 200
    assert "X-PAYMENT-RESPONSE" not in r.headers


def test_test_mode_without_payment_returns_402(paid_client):
    r = paid_client.post(URL, json=REQ)
    assert r.status_code == 402
    body = r.json()
    assert body["simulated"] is True
    assert body["x402Version"] == 1
    req = body["accepts"][0]
    assert req["network"] == "testnet"
    assert req["price_usd"] == "0.05"
    assert req["payTo"] == ""
    assert req["resource"] == URL


def test_test_mode_with_valid_test_payment_returns_200(paid_client):
    r = paid_client.post(URL, json=REQ, headers={"X-PAYMENT": build_test_payment_header("testnet")})
    assert r.status_code == 200
    assert r.json()["status"] == "NO_DATA_SOURCE"
    receipt = json.loads(base64.b64decode(r.headers["X-PAYMENT-RESPONSE"]))
    assert receipt == {"success": True, "simulated": True, "network": "testnet"}


@pytest.mark.parametrize(
    "header",
    [
        "garbage!!",
        _b64("not-a-dict"),
        _b64({"x402Version": 1, "scheme": "exact", "network": "testnet", "payload": {"test": True}}),
        _b64({"x402Version": 1, "scheme": "test", "network": "other-net", "payload": {"test": True}}),
        _b64({"x402Version": 2, "scheme": "test", "network": "testnet", "payload": {"test": True}}),
        _b64({"x402Version": 1, "scheme": "test", "network": "testnet", "payload": {"test": "yes"}}),
        "A" * 5000,
    ],
    ids=["garbage", "not-dict", "wrong-scheme", "wrong-network", "wrong-version",
         "payload-not-true", "too-large"],
)
def test_test_mode_rejects_invalid_payment(paid_client, header):
    r = paid_client.post(URL, json=REQ, headers={"X-PAYMENT": header})
    assert r.status_code == 402


def test_invalid_payment_mode_rejected():
    with pytest.raises(ConfigError):
        Settings(payment_mode="mainnet")


def test_mainnet_network_rejected():
    with pytest.raises(ConfigError):
        Settings(payment_mode="test", x402_network="solana-mainnet")


def test_price_configurable():
    c = make_client(payment_mode="test", price_usd=__import__("decimal").Decimal("0.10"))
    assert c.post(URL, json=REQ).json()["accepts"][0]["price_usd"] == "0.10"
