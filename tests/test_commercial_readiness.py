"""Commercial-readiness scenarios, full HTTP stack, x402 Mainnet profile.

What is REAL here: FastAPI app, x402-avm 2.0.2 middleware, the real
AlphaHunterProvider mapping code, the quality gate, quota and telemetry.
What is SIMULATED: the bridge HTTP response (httpx monkeypatch) and the
facilitator (in-memory, never touches Algorand). No payment, no network,
no Helius. Passing these tests does NOT prove real data quality.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from x402.mechanisms.avm import ALGORAND_MAINNET_CAIP2

from app.main import create_app
from tests.conftest import USDC_MINT, WSOL_MINT
from tests.evidence_helpers import tx_evidence
from tests.test_x402_testnet import (
    FakeFacilitator,
    fake_signature_header,
    get_requirements,
    mainnet_x402_settings,
)

URL = "/v1/token/investigate"
PREVIEW = "/v1/token/preview"
NONEXISTENT_MINT = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"


def good_payload(mint=WSOL_MINT, **overrides):
    payload = {
        "schema_version": "1.0",
        "mint": mint,
        "freshness": {"age_seconds": 60, "stale_after_seconds": 3600, "is_stale": False},
        "creator": {
            "address": "CreatorAddr",
            "profile": {"verified_launches": 3, "rugged_launches": 2, "trust_score": 12.0},
        },
        "wallet_activity": {"profile": {"total_events": 41}, "market": {"marketcap": 2_500_000.0}},
        "behavioral_findings": {
            "alerts": [{"reason": "creator_rugged_previous_launches", "count": 2}],
            "explanation": "Creator launched 3 tokens; 2 lost >90% liquidity within 24h of launch.",
        },
        "concentration": {"top_10_percent": 61},
        "evidence": [tx_evidence(mint, source="solana-rpc")],
        "data_quality": {"status": "FRESH", "limitations": []},
    }
    payload.update(overrides)
    return payload


def build(monkeypatch, bridge, facilitator=None, **settings):
    calls = {"bridge": 0}

    def get(self, url, *, headers):
        calls["bridge"] += 1
        return bridge(url)

    monkeypatch.setattr(httpx.Client, "get", get)
    fac = facilitator or FakeFacilitator(network=ALGORAND_MAINNET_CAIP2)
    app = create_app(mainnet_x402_settings(**settings), facilitator_client=fac)
    return TestClient(app, raise_server_exceptions=False), fac, calls


def paid(client, mint=WSOL_MINT, agent="agent-a"):
    req = get_requirements(client).accepts[0]
    return client.post(
        URL,
        json={"chain": "solana", "mint": mint},
        headers={"PAYMENT-SIGNATURE": fake_signature_header(req), "X-Agent-Id": agent},
    )


# ------------------------------------------------------------ valid, complete
def test_valid_mint_with_complete_evidence_settles_once(monkeypatch):
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=good_payload()))
    r = paid(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "FRESH"
    assert body["intelligence"]["evidence"][0]["mint"] == WSOL_MINT
    assert "explanation" in body["intelligence"]["behavioral_findings"]
    # observed outcomes exist -> the score is kept, not forced to UNKNOWN
    assert body["intelligence"]["creator"]["profile"]["trust_score"] == 12.0
    assert fac.verify_calls == 1 and fac.settle_calls == 1
    assert "PAYMENT-RESPONSE" in r.headers


# ------------------------------------------------------------ no-charge paths
def _assert_no_charge(r, fac, reason=None):
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] in ("intelligence_not_ready", "provider_unavailable", "provider_error")
    assert fac.settle_calls == 0
    assert "PAYMENT-RESPONSE" not in r.headers
    if reason:
        assert reason in r.json()["error"]["reasons"]


def test_known_mint_with_old_data_is_not_charged(monkeypatch):
    payload = good_payload(evidence=[tx_evidence(WSOL_MINT, age_seconds=10 * 86400)])
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    _assert_no_charge(paid(client), fac, "evidence_older_than_freshness_window")


def test_bridge_stale_label_is_not_charged(monkeypatch):
    payload = good_payload(
        freshness={"age_seconds": 2_745_044, "stale_after_seconds": 3600, "is_stale": True},
        data_quality={"status": "STALE", "limitations": ["Dados de mercado antigos: STALE."]},
    )
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    _assert_no_charge(paid(client), fac, "data_not_fresh")


def test_mint_without_evidence_is_not_charged(monkeypatch):
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=good_payload(evidence=[])))
    _assert_no_charge(paid(client), fac, "no_verifiable_transaction_evidence")


def test_current_bridge_contract_shape_is_not_chargeable(monkeypatch):
    """The bridge contract captured in test fixtures (source-only evidence,
    trust_score without outcomes, no explanation) must never be sold."""
    payload = good_payload(
        creator={"address": "creator-address", "profile": {"trust_score": 100.0}},
        wallet_activity={"market": {"marketcap": 1000000.0}, "recent_events": []},
        behavioral_findings={"scores": {"alpha_score": 89.0}, "decision": "WAIT", "alerts": [], "explanation": None},
        evidence=[{"source": "read-only-bridge"}],
    )
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    r = paid(client)
    _assert_no_charge(r, fac, "no_verifiable_transaction_evidence")
    assert "no_observed_behavioral_finding" in r.json()["error"]["reasons"]


def test_evidence_for_other_mint_is_not_charged(monkeypatch):
    payload = good_payload(evidence=[tx_evidence(USDC_MINT)])
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    _assert_no_charge(paid(client), fac, "evidence_not_linked_to_requested_mint")


def test_nonexistent_mint_is_not_charged(monkeypatch):
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(404, json={"detail": "not found"}))
    _assert_no_charge(paid(client, NONEXISTENT_MINT), fac, "data_not_fresh")


def test_invalid_mint_never_reaches_bridge_or_settlement(monkeypatch):
    client, fac, calls = build(monkeypatch, lambda _u: httpx.Response(200, json=good_payload()))
    req = get_requirements(client).accepts[0]
    r = client.post(URL, json={"chain": "solana", "mint": "0OIl" * 10},
                    headers={"PAYMENT-SIGNATURE": fake_signature_header(req)})
    assert r.status_code in (402, 422)
    assert fac.settle_calls == 0
    assert calls["bridge"] == 0


@pytest.mark.parametrize("failure", [
    lambda _u: (_ for _ in ()).throw(httpx.ConnectError("down")),
    lambda _u: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
    lambda _u: httpx.Response(500, text="boom"),
    lambda _u: httpx.Response(200, text="not json"),
    lambda _u: httpx.Response(200, json={"schema_version": "9.9"}),
])
def test_bridge_failure_is_not_charged_and_is_recorded(monkeypatch, failure):
    client, fac, _ = build(monkeypatch, failure)
    r = paid(client)
    assert r.status_code in (502, 503)
    assert fac.settle_calls == 0
    rows = client.app.state.cost_ledger.all_records()
    assert len(rows) == 1
    assert rows[0]["served_from"] == "error"
    assert rows[0]["revenue_kind"] == "none"


def test_bridge_mint_mismatch_is_rejected(monkeypatch):
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=good_payload(mint=USDC_MINT)))
    r = paid(client)
    assert r.status_code == 502
    assert fac.settle_calls == 0


# ------------------------------------------------------------ public payload hygiene
def test_unobserved_reputation_is_unknown_in_paid_response(monkeypatch):
    payload = good_payload(creator={"address": "C", "profile": {"trust_score": 100.0}})
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    r = paid(client)
    assert r.status_code == 200  # still sold: wallet events + alerts are observed
    creator = r.json()["intelligence"]["creator"]
    assert creator["reputation"] == "UNKNOWN"
    assert "trust_score" not in creator["profile"]
    assert any("UNKNOWN" in x for x in r.json()["data_quality"]["limitations"])


def test_contradictory_market_data_is_removed_and_flagged(monkeypatch):
    payload = good_payload(
        wallet_activity={"profile": {"total_events": 41}, "market": {"marketcap": 2_500_000.0}},
        concentration={"top_10_percent": 61, "market_cap": 900_000_000.0},
    )
    client, _, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    body = paid(client).json()
    assert body["intelligence"]["wallet_activity"]["market"]["marketcap"] is None
    assert body["intelligence"]["concentration"]["market_cap"] is None
    assert any("Contradictory market-cap" in x for x in body["data_quality"]["limitations"])


@pytest.mark.parametrize("bad", [0, -5, 1e15, "1000000", True])
def test_anomalous_marketcap_is_removed(monkeypatch, bad):
    payload = good_payload(wallet_activity={"profile": {"total_events": 41}, "market": {"marketcap": bad}})
    client, _, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    body = paid(client).json()
    assert body["intelligence"]["wallet_activity"]["market"]["marketcap"] is None
    assert any("anomalous market-cap" in x for x in body["data_quality"]["limitations"])


def test_internal_and_sensitive_fields_never_reach_buyer(monkeypatch):
    payload = good_payload(
        wallet_activity={
            "profile": {"total_events": 41},
            "bridge_token": "SECRET-123",
            "db_path": "/home/ahx/alpha_hunter.db",
            "_internal_score": 7,
            "note": "/root/ahx/alpha_hunter.db",
            "origin": "http://127.0.0.1:8787/v1",
        },
    )
    client, _, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    text = paid(client).text
    for leaked in ("SECRET-123", "alpha_hunter.db", "_internal_score", "127.0.0.1", "test-token"):
        assert leaked not in text


# ------------------------------------------------------------ free preview
def test_preview_is_free_and_predicts_premium_eligibility(monkeypatch):
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=good_payload()))
    r = client.post(PREVIEW, json={"chain": "solana", "mint": WSOL_MINT})
    assert r.status_code == 200
    assert r.json()["premium_eligible"] is True
    assert "intelligence" not in r.json()
    assert fac.verify_calls == 0 and fac.settle_calls == 0


def test_preview_explains_why_premium_would_not_be_chargeable(monkeypatch):
    payload = good_payload(evidence=[{"source": "read-only-bridge"}])
    client, _, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=payload))
    body = client.post(PREVIEW, json={"chain": "solana", "mint": WSOL_MINT}).json()
    assert body["premium_eligible"] is False
    assert body["premium_ineligibility_reasons"] == ["no_verifiable_transaction_evidence"]


def test_preview_abuse_is_rate_limited_and_bridge_is_protected(monkeypatch):
    client, _, calls = build(
        monkeypatch, lambda _u: httpx.Response(200, json=good_payload()),
        quota_anonymous_per_minute=3, quota_anonymous_per_day=5,
    )
    codes = [client.post(PREVIEW, json={"chain": "solana", "mint": WSOL_MINT}).status_code for _ in range(10)]
    assert codes[:3] == [200, 200, 200]
    assert set(codes[3:]) == {429}
    assert calls["bridge"] == 1  # cache absorbed repeated calls


def test_preview_distinct_mint_scraping_is_limited(monkeypatch):
    client, _, calls = build(
        monkeypatch, lambda u: httpx.Response(404),
        quota_anonymous_distinct_mints_per_day=2,
    )
    from tests.evidence_helpers import b58encode
    mints = [b58encode(bytes([i + 1]) * 32) for i in range(5)]
    codes = [client.post(PREVIEW, json={"chain": "solana", "mint": m}).status_code for m in mints]
    assert codes[:2] == [200, 200]
    assert set(codes[2:]) == {429}
    assert calls["bridge"] == 2


# ------------------------------------------------------------ telemetry and budget
def test_telemetry_matches_outcomes_and_zero_helius(monkeypatch):
    state = {"payload": good_payload()}
    client, fac, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=state["payload"]))
    assert paid(client, agent="agent-a").status_code == 200
    state["payload"] = good_payload(evidence=[])
    assert paid(client, agent="agent-b").status_code == 503
    assert client.post(PREVIEW, json={"chain": "solana", "mint": WSOL_MINT}).status_code == 200

    ledger = client.app.state.cost_ledger
    rows = {r["agent_id"] or "preview": r for r in ledger.all_records()}
    assert rows["agent-a"]["revenue_kind"] == "settled"
    assert rows["agent-a"]["revenue_usd"] == "0.05"
    assert rows["agent-b"]["revenue_kind"] == "none"
    assert rows["agent-b"]["revenue_usd"] is None
    totals = ledger.totals_all()
    assert totals["query_count"] == 3
    assert totals["revenue_usd_total"] == "0.05"
    assert totals["helius_calls_total"] == 0
    assert fac.settle_calls == 1


def test_failed_settlement_never_counts_as_revenue(monkeypatch):
    fac = FakeFacilitator(network=ALGORAND_MAINNET_CAIP2, settle_ok=False)
    client, _, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=good_payload()), facilitator=fac)
    r = paid(client)
    assert r.status_code == 402
    assert client.app.state.cost_ledger.totals_all()["revenue_usd_total"] == "0"


def test_version_exposes_deployed_commit(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567890")
    client, _, _ = build(monkeypatch, lambda _u: httpx.Response(404))
    assert client.request("GET", "/version").json()["commit"] == "abcdef123456"


def test_operator_report_reads_ledger_without_writing(monkeypatch, tmp_path):
    import os

    from scripts.telemetry_report import report

    db = tmp_path / "telemetry.db"
    client, _, _ = build(monkeypatch, lambda _u: httpx.Response(200, json=good_payload()),
                         telemetry_db_path=str(db))
    assert paid(client, agent="agent-a").status_code == 200
    before = os.path.getmtime(db)
    out = report(str(db))
    assert out["settled_sales"] == 1
    assert out["settled_revenue_usd"] == "0.05"
    assert out["distinct_paying_declared_agents"] == 1
    assert out["helius_calls_total"] == 0
    assert os.path.getmtime(db) == before
