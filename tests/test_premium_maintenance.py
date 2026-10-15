"""Premium maintenance mode: real money can never be taken and premium
intelligence can never be served while sales are suspended.

Zero network: in-memory facilitator, counting provider.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from x402.mechanisms.avm import ALGORAND_MAINNET_CAIP2

from app.config import Settings, load_settings
from app.main import create_app
from app.models import DataQuality, Intelligence
from app.payments.gate import build_test_payment_header
from app.providers.base import ProviderResult
from tests.conftest import WSOL_MINT
from tests.test_x402_testnet import (
    FakeFacilitator,
    fake_signature_header,
    mainnet_x402_settings,
    x402_settings,
)

URL = "/v1/token/investigate"
REQ = {"chain": "solana", "mint": WSOL_MINT}


class CountingProvider:
    name = "alpha_hunter"

    def __init__(self):
        self.calls = 0

    def investigate(self, chain, mint):
        self.calls += 1
        return ProviderResult(
            status="FRESH",
            intelligence=Intelligence(creator={"address": "PREMIUM-SECRET-CREATOR"},
                                      evidence=[{"signature": "PREMIUM-SECRET-SIG"}]),
            data_quality=DataQuality(status="FRESH"),
        )


def _requirements_from_enabled_app(settings_factory, network):
    """Build a well-formed payment header against an ENABLED twin app, the
    way a real buyer who cached an old offer would."""
    fac = FakeFacilitator(network=network)
    twin = TestClient(create_app(settings_factory(premium_sales_enabled=True),
                                 provider=CountingProvider(), facilitator_client=fac),
                      raise_server_exceptions=False)
    from x402.http import decode_payment_required_header
    r = twin.post(URL, json=REQ)
    return decode_payment_required_header(r.headers["PAYMENT-REQUIRED"]).accepts[0]


def _assert_blocked(r, provider, fac=None):
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "premium_maintenance"
    assert r.headers.get("Retry-After") == "3600"
    assert "PAYMENT-REQUIRED" not in r.headers
    assert "PAYMENT-RESPONSE" not in r.headers
    assert "X-PAYMENT-RESPONSE" not in r.headers
    assert "PREMIUM-SECRET" not in r.text and "intelligence" not in body
    assert provider.calls == 0
    if fac is not None:
        assert fac.verify_calls == 0 and fac.settle_calls == 0


@pytest.mark.parametrize("profile", ["mainnet", "testnet"])
def test_x402_profiles_block_every_payment_header_shape(profile):
    if profile == "mainnet":
        factory, network = mainnet_x402_settings, ALGORAND_MAINNET_CAIP2
    else:
        from x402.mechanisms.avm import ALGORAND_TESTNET_CAIP2
        factory, network = x402_settings, ALGORAND_TESTNET_CAIP2
    good_header = fake_signature_header(_requirements_from_enabled_app(factory, network))

    provider, fac = CountingProvider(), FakeFacilitator(network=network)
    client = TestClient(create_app(factory(premium_sales_enabled=False), provider=provider,
                                   facilitator_client=fac), raise_server_exceptions=False)
    headers_cases = [
        {},                                         # absent
        {"PAYMENT-SIGNATURE": good_header},         # well-formed
        {"PAYMENT-SIGNATURE": "garbage!!"},         # invalid
        {"PAYMENT-SIGNATURE": good_header},         # reused
        {"X-PAYMENT": good_header},                 # legacy header name
    ]
    for headers in headers_cases:
        _assert_blocked(client.post(URL, json=REQ, headers=headers), provider, fac)


@pytest.mark.parametrize("mode", ["disabled", "test"])
def test_non_x402_modes_do_not_leak_premium_for_free(mode):
    """PAYMENT_MODE=disabled lets everything through in PaymentGate; the
    maintenance switch must still block it."""
    provider = CountingProvider()
    client = TestClient(create_app(Settings(payment_mode=mode, premium_sales_enabled=False),
                                   provider=provider), raise_server_exceptions=False)
    _assert_blocked(client.post(URL, json=REQ), provider)
    _assert_blocked(client.post(URL, json=REQ, headers={"X-PAYMENT": build_test_payment_header("testnet")}), provider)


def test_invalid_body_is_also_blocked_without_processing():
    provider = CountingProvider()
    client = TestClient(create_app(Settings(premium_sales_enabled=False), provider=provider),
                        raise_server_exceptions=False)
    _assert_blocked(client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"}), provider)


def test_public_routes_stay_up_and_discovery_says_maintenance():
    fac = FakeFacilitator(network=ALGORAND_MAINNET_CAIP2)
    client = TestClient(create_app(mainnet_x402_settings(premium_sales_enabled=False),
                                   provider=CountingProvider(), facilitator_client=fac),
                        raise_server_exceptions=False)
    for path in ("/health", "/ready", "/openapi.json", "/"):
        assert client.get(path).status_code == 200, path
    version = client.get("/version").json()
    assert version["premium_sales"] == "maintenance"
    assert version["payment_mode"] == "x402-mainnet"
    manifest = client.get("/.well-known/x402").json()
    assert manifest["sales_status"] == "maintenance"
    paid = next(r for r in manifest["resources"] if r["path"] == URL)
    assert paid["sales_status"] == "maintenance"
    assert fac.verify_calls == 0 and fac.settle_calls == 0


def test_reenabling_restores_normal_paid_flow():
    fac = FakeFacilitator(network=ALGORAND_MAINNET_CAIP2)
    client = TestClient(create_app(mainnet_x402_settings(premium_sales_enabled=True),
                                   provider=CountingProvider(), facilitator_client=fac),
                        raise_server_exceptions=False)
    r = client.post(URL, json=REQ)
    assert r.status_code == 402
    assert "PAYMENT-REQUIRED" in r.headers
    assert client.get("/version").json()["premium_sales"] == "available"


@pytest.mark.parametrize("raw,expected", [
    (None, False), ("", False), ("false", False), ("0", False), ("yes", False),
    ("1", False), ("enabled", False), (" true ", True), ("TRUE", True), ("true", True),
])
def test_only_explicit_true_enables_sales(monkeypatch, raw, expected):
    monkeypatch.delenv("PREMIUM_SALES_ENABLED", raising=False)
    if raw is not None:
        monkeypatch.setenv("PREMIUM_SALES_ENABLED", raw)
    assert load_settings(env_file=None).premium_sales_enabled is expected


def test_default_runtime_configuration_is_maintenance(monkeypatch):
    monkeypatch.delenv("PREMIUM_SALES_ENABLED", raising=False)
    assert Settings().premium_sales_enabled is False
    assert load_settings(env_file=None).premium_sales_enabled is False


def test_free_preview_still_works_and_says_not_eligible_during_maintenance(monkeypatch):
    import httpx

    from tests.evidence_helpers import tx_evidence

    payload = {
        "schema_version": "1.0", "mint": WSOL_MINT,
        "freshness": {"age_seconds": 30, "stale_after_seconds": 3600, "is_stale": False},
        "creator": {"address": "C", "profile": {"verified_launches": 2}},
        "wallet_activity": {"profile": {"total_events": 5}},
        "behavioral_findings": {"alerts": [{"reason": "x"}], "explanation": "Observed alert."},
        "evidence": [tx_evidence(WSOL_MINT)],
        "data_quality": {"status": "FRESH", "limitations": []},
    }
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, *, headers: httpx.Response(200, json=payload))
    fac = FakeFacilitator(network=ALGORAND_MAINNET_CAIP2)
    client = TestClient(create_app(mainnet_x402_settings(premium_sales_enabled=False), facilitator_client=fac),
                        raise_server_exceptions=False)
    body = client.post("/v1/token/preview", json=REQ).json()
    assert body["premium_eligible"] is False
    assert body["premium_ineligibility_reasons"] == ["premium_sales_maintenance"]
    assert "intelligence" not in body
    assert fac.verify_calls == 0 and fac.settle_calls == 0
