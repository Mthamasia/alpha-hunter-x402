import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
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
        "premium_eligible", "premium_ineligibility_reasons",
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
        quota_anonymous_per_minute=2,
    )
    client = TestClient(app)

    r1 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    r2 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    r3 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r3.headers["Retry-After"]
    assert r3.json()["error"]["reason"] == "per_minute_limit_exceeded"
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


def test_trusted_proxy_header_requires_trusted_proxy_cidrs():
    with pytest.raises(ConfigError, match="TRUSTED_PROXY_CIDRS"):
        Settings(trusted_proxy_header="X-Forwarded-For")


def test_trusted_proxy_cidrs_rejects_garbage():
    with pytest.raises(ConfigError, match="TRUSTED_PROXY_CIDRS"):
        Settings(trusted_proxy_header="X-Forwarded-For", trusted_proxy_cidrs=("not-a-cidr",))


def test_forwarded_header_ignored_when_peer_is_not_a_trusted_proxy(monkeypatch):
    """A header NAME being configured must not be enough on its own: the
    immediate TCP peer (TestClient's default, 'testclient') is not inside
    any trusted CIDR here, so the header must be ignored."""

    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        trusted_proxy_header="X-Forwarded-For",
        trusted_proxy_cidrs=("10.0.0.0/8",),
        quota_anonymous_per_minute=1,
    )
    client = TestClient(app)

    # Two different claimed X-Forwarded-For values from the SAME untrusted
    # peer must share one quota bucket (the header is ignored), not two.
    r1 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers={"X-Forwarded-For": "1.1.1.1"})
    r2 = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers={"X-Forwarded-For": "2.2.2.2"})

    assert r1.status_code == 200
    assert r2.status_code == 429  # blocked under the same real-peer bucket, header spoofing had no effect


def test_quota_subject_never_stores_the_raw_ip(monkeypatch, tmp_path):
    quota_db = str(tmp_path / "quota.db")
    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        quota_db_path=quota_db,
    )
    client = TestClient(app)
    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    import sqlite3

    conn = sqlite3.connect(quota_db)
    subjects = [r[0] for r in conn.execute("SELECT DISTINCT subject_id FROM quota_minute")]
    conn.close()

    assert subjects  # at least one subject recorded
    assert all("127.0.0.1" not in s and "testclient" not in s for s in subjects)
    assert all(s.startswith("ip:") and len(s) == len("ip:") + 64 for s in subjects)  # sha256 hex


def test_preview_never_calls_helius_on_any_path(monkeypatch):
    """Scope of this test, precisely: this process (the Railway satellite)
    never has any code path that calls Helius directly, under any
    preview outcome -- success, cache hit, unknown mint, invalid mint,
    bridge 401/404/500, quota-blocked. It fails if any of those paths
    opens a socket to anything other than loopback (the bridge call
    itself is monkeypatched here, same as the rest of this file -- a
    real `httpx.Client.get` to the live bridge is network, which
    conftest.block_network already forbids everywhere in this suite).

    What this test does NOT and CANNOT prove: that the real, deployed
    AHX bridge on the VPS -- a different process, different repo --
    never calls Helius either. That guarantee lives in the AHX bridge's
    own repository (its audit-hook tests assert zero RPC/websocket
    imports and zero non-SQLite network connections when it handles a
    request) and was independently re-verified read-only against the
    live bridge; see docs/vps-bridge-read-only-audit.md for that
    evidence. Both halves are needed for the full claim; this test is
    only the Railway half.
    """

    import sys

    seen_non_loopback = []

    def audit_hook(event, args):
        if event in ("socket.connect", "socket.getaddrinfo"):
            addr = args[0] if event == "socket.getaddrinfo" else args[1]
            host = addr[0] if isinstance(addr, tuple) else addr
            if host not in ("127.0.0.1", "::1", "localhost", None):
                seen_non_loopback.append((event, args))

    sys.addaudithook(audit_hook)  # audit hooks cannot be removed; fine for one test process

    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        quota_anonymous_per_minute=2,
        quota_anonymous_distinct_mints_per_day=1,
    )
    client = TestClient(app)

    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})  # success, cache miss
    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})  # success, cache hit
    client.post(URL, json={"chain": "solana", "mint": "not-a-real-mint"})  # 422
    client.post(URL, json={"chain": "solana", "mint": UNKNOWN_MINT})  # distinct-mint quota block (429)

    app_401, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(401))
    TestClient(app_401).post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    app_500, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(500))
    TestClient(app_500).post(URL, json={"chain": "solana", "mint": WSOL_MINT})

    assert seen_non_loopback == []


def test_preview_api_key_gets_registered_tier_limits(monkeypatch):
    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        quota_anonymous_per_minute=1,
        quota_registered_per_minute=3,
    )
    client = TestClient(app)
    issued = app.state.quota_store.issue_api_key("agent-alpha")

    # Anonymous: blocked on the 2nd request (limit 1/min).
    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    assert client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}).status_code == 429

    # With a valid API key: allowed up to its own, higher limit (3/min),
    # unaffected by the anonymous IP's exhausted quota.
    headers = {"X-API-Key": issued.raw_key}
    assert client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers=headers).status_code == 200
    assert client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers=headers).status_code == 200
    assert client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers=headers).status_code == 200
    assert client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers=headers).status_code == 429


def test_preview_revoked_api_key_falls_back_to_anonymous_tier(monkeypatch):
    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        quota_anonymous_per_minute=1,
        quota_registered_per_minute=5,
    )
    client = TestClient(app)
    issued = app.state.quota_store.issue_api_key("agent-alpha")
    app.state.quota_store.revoke_api_key(issued.key_id)

    headers = {"X-API-Key": issued.raw_key}
    assert client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers=headers).status_code == 200
    # Falls back to the anonymous limit (1/min), not the revoked key's tier.
    assert client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}, headers=headers).status_code == 429


def test_preview_fails_closed_when_quota_store_is_unavailable(monkeypatch):
    import sqlite3

    app, _ = make_app(monkeypatch, lambda _u, _h: httpx.Response(200, json=bridge_payload()))

    def broken_connect(self):
        raise sqlite3.OperationalError("disk I/O error")

    from app.quota import QuotaStore

    monkeypatch.setattr(QuotaStore, "_connect", broken_connect)
    client = TestClient(app)

    r = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    assert r.status_code == 429  # fails closed, never unlimited
    assert r.json()["error"]["reason"] == "quota_store_unavailable"


def test_blocked_preview_requests_are_recorded_separately_from_successful_ones(monkeypatch):
    app, _ = make_app(
        monkeypatch,
        lambda _u, _h: httpx.Response(200, json=bridge_payload()),
        quota_anonymous_per_minute=1,
    )
    client = TestClient(app)

    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})  # blocked

    from app.telemetry import SERVED_FROM_BLOCKED, SERVED_FROM_BRIDGE

    rows = app.state.cost_ledger.all_records()
    served_from = sorted(r["served_from"] for r in rows)
    assert served_from == sorted([SERVED_FROM_BRIDGE, SERVED_FROM_BLOCKED])


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
