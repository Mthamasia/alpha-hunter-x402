import time

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import WSOL_MINT

URL = "/v1/token/preview"
INVESTIGATE_URL = "/v1/token/investigate"
SERVICE_TOKEN = "preview-test-service-token-must-not-leak"
UNKNOWN_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def bridge_payload(**overrides):
    payload = {
        "schema_version": "1.0",
        "mint": WSOL_MINT,
        "source": "alpha_hunter_x",
        "freshness": {
            "market_data_updated_at": "2026-09-24T12:00:00Z",
            "age_seconds": 30,
            "stale_after_seconds": 3600,
            "is_stale": False,
            "last_activity_at": "2026-09-24T12:00:00Z",
        },
        "creator": {"address": "creator-address", "profile": {"trust_score": 100.0}},
        "wallet_activity": {"market": {"marketcap": 1000000.0}, "recent_events": [], "related_wallets": []},
        "behavioral_findings": {"scores": {"alpha_score": 89.0}, "decision": "WAIT", "alerts": []},
        "concentration": {"top_10_percent": 42},
        "evidence": [{"source": "read-only-bridge"}],
        "data_quality": {"status": "FRESH", "limitations": []},
    }
    payload.update(overrides)
    return payload


def make_app(monkeypatch, handler, **settings_overrides):
    calls = {"count": 0}

    def get(self, url, *, headers):
        calls["count"] += 1
        return handler(url, headers)

    monkeypatch.setattr(httpx.Client, "get", get)

    defaults = dict(
        payment_mode="disabled",
        intelligence_provider="alpha_hunter",
        ahx_bridge_service_token=SERVICE_TOKEN,
    )
    defaults.update(settings_overrides)
    settings = Settings(**defaults)
    return create_app(settings), calls


def test_preview_valid_mint_returns_limited_summary_without_premium_fields(monkeypatch):
    app, calls = make_app(monkeypatch, lambda _u, _h: httpx.Response(200, json=bridge_payload()))
    client = TestClient(app)

    r = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "schema_version", "request_id", "chain", "mint", "generated_at",
        "available", "status", "age_seconds", "is_stale", "data_quality",
    }
    assert body["available"] is True
    assert body["status"] == "FRESH"
    assert body["age_seconds"] == 30
    assert body["is_stale"] is False
    assert body["data_quality"] == {"status": "FRESH", "limitations": []}
    # The premium payload must never leak through the free endpoint.
    for forbidden in ("intelligence", "creator", "wallet_activity", "behavioral_findings", "evidence", "concentration"):
        assert forbidden not in body
    assert calls["count"] == 1


def test_preview_stale_classified_correctly_not_forced_fresh(monkeypatch):
    payload = bridge_payload(
        freshness={
            "market_data_updated_at": "2026-08-23T22:28:49Z",
            "age_seconds": 2745044,
            "stale_after_seconds": 3600,
            "is_stale": True,
            "last_activity_at": "2026-08-23T22:28:49Z",
        },
        data_quality={"status": "STALE", "limitations": ["old data"]},
    )
    app, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(200, json=payload))
    client = TestClient(app)

    body = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}).json()

    assert body["status"] == "STALE"
    assert body["is_stale"] is True
    assert body["age_seconds"] == 2745044
    assert body["data_quality"]["status"] == "STALE"


def test_preview_unknown_mint_reports_unavailable(monkeypatch):
    app, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(404))
    client = TestClient(app)

    body = client.post(URL, json={"chain": "solana", "mint": UNKNOWN_MINT}).json()

    assert body["available"] is False
    assert body["status"] == "NO_DATA"
    assert body["age_seconds"] is None
    assert body["is_stale"] is None


def test_preview_invalid_mint_rejected(monkeypatch):
    app, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(200, json=bridge_payload()))
    client = TestClient(app)

    r = client.post(URL, json={"chain": "solana", "mint": "not-a-real-mint"})
    assert r.status_code == 422


def test_preview_bridge_auth_failure_surfaces_as_server_error_not_silently_served(monkeypatch):
    app, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(401))
    client = TestClient(app)

    r = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "provider_configuration_error"
    assert SERVICE_TOKEN not in r.text


def test_preview_caches_and_only_calls_bridge_once_within_ttl(monkeypatch):
    app, calls = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        preview_cache_ttl_seconds=60,
    )
    client = TestClient(app)

    first = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    second = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "FRESH"
    assert calls["count"] == 1  # second request was served from cache


def test_preview_cache_expires_after_ttl(monkeypatch):
    app, calls = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        preview_cache_ttl_seconds=0.05,
    )
    client = TestClient(app)

    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    time.sleep(0.1)
    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    assert calls["count"] == 2


def test_preview_rate_limited_after_threshold(monkeypatch):
    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        preview_rate_limit_per_minute=2,
    )
    client = TestClient(app)

    r1 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    r2 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    r3 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "rate_limited"


def test_preview_telemetry_records_zero_helius_calls(monkeypatch):
    app, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(200, json=bridge_payload()))
    client = TestClient(app)

    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})  # cache miss -> bridge
    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})  # cache hit

    totals = app.state.cost_ledger.totals_by_mint(WSOL_MINT)
    assert totals["query_count"] == 2
    assert totals["helius_calls_total"] == 0
    assert totals["cost_usd_estimated_total"] == "0"
    assert totals["revenue_usd_total"] == "0"  # preview is free: no revenue recorded


def test_preview_telemetry_records_agent_id_from_header(monkeypatch):
    app, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(200, json=bridge_payload()))
    client = TestClient(app)

    client.post(
        URL,
        json={"chain": "solana", "mint": WSOL_MINT},
        headers={"X-Agent-Id": "agent-xyz"},
    )

    totals = app.state.cost_ledger.totals_by_agent("agent-xyz")
    assert totals["query_count"] == 1


def test_investigate_route_still_works_and_records_revenue(monkeypatch):
    """Regression: the existing paid route must be unaffected by the new
    preview endpoint and telemetry wiring."""

    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        payment_mode="test",
        x402_network="testnet",
    )
    client = TestClient(app)

    from app.payments.gate import build_test_payment_header

    r = client.post(
        INVESTIGATE_URL,
        json={"chain": "solana", "mint": WSOL_MINT},
        headers={"X-PAYMENT": build_test_payment_header("testnet")},
    )

    assert r.status_code == 200
    body = r.json()
    assert "intelligence" in body  # paid route still returns the full payload
    assert body["intelligence"]["creator"]["address"] == "creator-address"

    totals = app.state.cost_ledger.totals_by_mint(WSOL_MINT)
    assert totals["query_count"] == 1
    assert totals["revenue_usd_total"] == "0.05"
