"""Mainnet paid-result quality regressions. Zero network, zero Helius.

A false 200 from this guard could trigger x402 settlement for a fabricated
or empty report, even when the upstream bridge calls the report FRESH.

Originally PR #7. Tightened during commercial-readiness reconciliation:
a reference now has to be a well-formed Solana transaction signature (or an
explorer URL carrying one), explicitly linked to the requested mint and
carrying an on-chain timestamp that is recent by THIS server's clock.
Internal IDs, slots alone and free-form "references" are no longer enough.
"""
from types import SimpleNamespace as Obj

import pytest

from app.main import _billable_intelligence
from app.quality import (
    REASON_FUTURE,
    REASON_NO_FINDING,
    REASON_NO_TIMESTAMP,
    REASON_NO_VERIFIABLE_TX,
    REASON_NOT_FRESH,
    REASON_NOT_LINKED_TO_MINT,
    REASON_TOO_OLD,
    assess_premium_eligibility,
)
from tests.conftest import USDC_MINT, WSOL_MINT
from tests.evidence_helpers import signature, tx_evidence

MAX_AGE = 86400


def billable(result, mint=WSOL_MINT):
    return _billable_intelligence(result, mint, max_evidence_age_seconds=MAX_AGE)


def reasons(result, mint=WSOL_MINT):
    return assess_premium_eligibility(result, mint, max_evidence_age_seconds=MAX_AGE).reasons


def sample(*, evidence=None, creator=None, wallet=None, findings=None,
           concentration=None, quality="FRESH"):
    return Obj(
        data_quality=Obj(status=quality),
        intelligence=Obj(
            evidence=evidence if evidence is not None else [],
            creator=creator,
            wallet_activity=wallet if wallet is not None else {},
            behavioral_findings=findings if findings is not None else {},
            concentration=concentration,
        ),
    )


def trace():
    return [tx_evidence(WSOL_MINT, source="Solana RPC")]


CREATOR = {"address": "creator-address", "profile": {"verified_launches": 2}}
WALLET = {"profile": {"total_events": 4}}


@pytest.mark.parametrize("item", [
    {}, {"source": "bridge-readonly"}, {"source": "Solana RPC", "id": "123"},
    {"signature": ""}, {"tx_hash": "    "}, {"tx_id": "UNKNOWN"},
    {"slot": 0}, {"slot": True}, {"slot": False}, {"slot": -1},
    "transaction", None, [], 123,
    # Accepted by PR #7, rejected now: not independently verifiable.
    {"transaction_signature": "3kJmTxProof", "mint": WSOL_MINT, "block_time": 1_760_000_000},
    {"event_id": "immutable-evidence-record", "mint": WSOL_MINT},
    {"reference": "anything", "mint": WSOL_MINT},
    {"url": "https://solscan.io/tx/example", "mint": WSOL_MINT},
    {"url": "https://evil.example/tx/" + signature(), "mint": WSOL_MINT},
    {"url": "http://solscan.io/tx/" + signature(), "mint": WSOL_MINT},
    {"slot": 424242, "mint": WSOL_MINT},
])
def test_nontraceable_proof_must_not_allow_mainnet_settlement(item):
    result = sample(evidence=[item], creator=CREATOR)
    assert not billable(result)
    assert REASON_NO_VERIFIABLE_TX in reasons(result)


@pytest.mark.parametrize("quality", ["STALE", "NO_DATA", "UNKNOWN", "UNAVAILABLE", "", None])
def test_nonfresh_quality_denies_even_real_event(quality):
    result = sample(quality=quality, evidence=trace(), wallet=WALLET)
    assert not billable(result)
    assert reasons(result) == [REASON_NOT_FRESH]


@pytest.mark.parametrize("evidence", [[], [None], [{}], ["synthetic"]])
def test_no_verifiable_provenance_denies(evidence):
    assert not billable(sample(evidence=evidence, wallet=WALLET))


@pytest.mark.parametrize("candidate", [
    {"creator": {"address": "creator-address"}},
    {"creator": {"address": "creator-address", "profile": {"trust_score": 100}}},
    {"creator": {"address": "creator-address", "profile": {"verified_launches": 0}}},
    {"creator": {"address": "creator-address", "profile": {"verified_launches": True}}},
    {"wallet": {"market": {"marketcap": 999999999999999}}},
    {"wallet": {"profile": {"total_events": 0}}},
    {"wallet": {"profile": {"total_events": True}}},
    {"wallet": {"recent_events": [{}]}},
    {"findings": {"scores": {"alpha_score": 99, "risk_score": 0}}},
    {"findings": {"decision": "WAIT", "explanation": None, "alerts": []}},
    {"concentration": {"top_10_percent": 55}},
])
def test_placeholder_or_unexplained_score_denies_payment(candidate):
    result = sample(evidence=trace(), **candidate)
    assert not billable(result)
    assert reasons(result) == [REASON_NO_FINDING]


@pytest.mark.parametrize("candidate", [
    {"creator": CREATOR},
    {"creator": {"profile": {"observed_launches": 1}}},
    {"creator": {"profile": {"confirmed_outcomes": 1}}},
    {"wallet": WALLET},
    {"wallet": {"recent_events": [{"signature": signature("evt")}]}},
    {"findings": {"alerts": [{"reason": "observed-wallet-cluster"}]}},
    {"findings": {"explanation": "On-chain wallet overlap observed across launches"}},
])
def test_provenance_plus_observed_finding_can_be_billable(candidate):
    assert billable(sample(evidence=trace(), **candidate))


@pytest.mark.parametrize("key", ["transaction_signature", "tx_signature", "signature"])
def test_signature_keys_accepted(key):
    item = {key: signature(), "mint": WSOL_MINT, "block_time": tx_evidence(WSOL_MINT)["block_time"]}
    assert billable(sample(evidence=[item], wallet=WALLET))


@pytest.mark.parametrize("host", ["solscan.io", "explorer.solana.com", "solana.fm"])
def test_explorer_url_with_real_signature_accepted(host):
    item = {"url": f"https://{host}/tx/{signature()}", "mint": WSOL_MINT,
            "block_time": tx_evidence(WSOL_MINT)["block_time"]}
    assert billable(sample(evidence=[item], wallet=WALLET))


def test_evidence_list_only_one_traceable_entry_needed():
    assert billable(sample(evidence=[{}, {"source": "bridge"}, trace()[0]], creator=CREATOR))


def test_evidence_for_another_mint_is_not_evidence_for_this_one():
    result = sample(evidence=[tx_evidence(USDC_MINT)], creator=CREATOR)
    assert not billable(result)
    assert REASON_NOT_LINKED_TO_MINT in reasons(result)


def test_evidence_without_mint_link_is_rejected():
    item = tx_evidence(WSOL_MINT)
    item.pop("mint")
    assert REASON_NOT_LINKED_TO_MINT in reasons(sample(evidence=[item], creator=CREATOR))


@pytest.mark.parametrize("block_time", [None, "yesterday", "2026-10-01T00:00:00", 0, 12345, True])
def test_missing_or_invalid_onchain_time_is_rejected(block_time):
    item = tx_evidence(WSOL_MINT, block_time=block_time)
    assert REASON_NO_TIMESTAMP in reasons(sample(evidence=[item], creator=CREATOR))


def test_bridge_says_fresh_but_evidence_is_old():
    """Independent chronology: the bridge FRESH label is not trusted alone."""
    result = sample(evidence=[tx_evidence(WSOL_MINT, age_seconds=3 * 86400)], creator=CREATOR)
    assert not billable(result)
    assert reasons(result) == [REASON_TOO_OLD]


def test_future_timestamp_is_rejected():
    result = sample(evidence=[tx_evidence(WSOL_MINT, age_seconds=-3600)], creator=CREATOR)
    assert reasons(result) == [REASON_FUTURE]


def test_iso_timestamp_with_timezone_accepted():
    from datetime import datetime, timedelta, timezone
    item = tx_evidence(WSOL_MINT)
    item.pop("block_time")
    item["timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert billable(sample(evidence=[item], creator=CREATOR))


def test_no_intelligence_denies_mainnet():
    assert not billable(Obj(data_quality=Obj(status="FRESH"), intelligence=None))


def test_none_quality_denies_mainnet():
    assert not billable(Obj(data_quality=None, intelligence=Obj(evidence=trace())))


def test_text_only_evidence_is_not_sufficient():
    assert not billable(sample(evidence=["verified onchain"], wallet=WALLET))
