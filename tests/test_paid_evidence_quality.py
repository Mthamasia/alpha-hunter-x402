"""Mainnet paid-result quality regressions. Zero network, zero Helius.

A false 200 from this guard could trigger x402 settlement for a fabricated
or empty report, even when the upstream bridge calls the report FRESH.
"""
from types import SimpleNamespace as Obj

import pytest

from app.main import _billable_intelligence


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


TRACE = [{"source": "Solana RPC", "transaction_signature": "3kJmTxProof"}]
CREATOR = {"address": "creator-address", "profile": {"verified_launches": 2}}
WALLET = {"profile": {"total_events": 4}}


@pytest.mark.parametrize("item", [
    {}, {"source": "bridge-readonly"}, {"source": "Solana RPC", "id": "123"},
    {"signature": ""}, {"tx_hash": "    "}, {"tx_id": "UNKNOWN"},
    {"slot": 0}, {"slot": True}, {"slot": False}, {"slot": -1},
    "transaction", None, [], 123,
])
def test_nontraceable_proof_must_not_allow_mainnet_settlement(item):
    assert not _billable_intelligence(sample(evidence=[item], creator=CREATOR))


@pytest.mark.parametrize("quality", ["STALE", "NO_DATA", "UNKNOWN", "UNAVAILABLE", "", None])
def test_nonfresh_quality_denies_even_real_event(quality):
    assert not _billable_intelligence(sample(quality=quality, evidence=TRACE, wallet=WALLET))


@pytest.mark.parametrize("evidence", [[], [None], [{}], ["synthetic"]])
def test_no_verifiable_provenance_denies(evidence):
    assert not _billable_intelligence(sample(evidence=evidence, wallet=WALLET))


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
    assert not _billable_intelligence(sample(evidence=TRACE, **candidate))


@pytest.mark.parametrize("candidate", [
    {"creator": CREATOR},
    {"creator": {"profile": {"observed_launches": 1}}},
    {"creator": {"profile": {"confirmed_outcomes": 1}}},
    {"wallet": WALLET},
    {"wallet": {"recent_events": [{"signature": "3kJmTxProof"}]}},
    {"findings": {"alerts": [{"reason": "observed-wallet-cluster"}]}},
    {"findings": {"explanation": "On-chain wallet overlap observed across launches"}},
])
def test_provenance_plus_observed_finding_can_be_billable(candidate):
    assert _billable_intelligence(sample(evidence=TRACE, **candidate))


@pytest.mark.parametrize("proof", [
    {"transaction_signature": "3kJmTxProof"},
    {"tx_signature": "3kJmTxProof"},
    {"signature": "3kJmTxProof"},
    {"tx_id": "3kJmTxProof"},
    {"tx_hash": "3kJmTxProof"},
    {"transaction_id": "3kJmTxProof"},
    {"event_id": "immutable-evidence-record"},
    {"reference": "https://solscan.io/tx/example"},
    {"url": "https://solscan.io/tx/example"},
    {"slot": 424242},
])
def test_accepted_trace_references_with_wallet_activity(proof):
    assert _billable_intelligence(sample(evidence=[proof], wallet=WALLET))


def test_evidence_list_only_one_traceable_entry_needed():
    assert _billable_intelligence(
        sample(evidence=[{}, {"source": "bridge"}, TRACE[0]], creator=CREATOR))


def test_no_intelligence_denies_mainnet():
    assert not _billable_intelligence(Obj(data_quality=Obj(status="FRESH"), intelligence=None))


def test_none_quality_denies_mainnet():
    assert not _billable_intelligence(Obj(data_quality=None, intelligence=Obj(evidence=TRACE)))


def test_text_only_evidence_is_not_sufficient():
    assert not _billable_intelligence(sample(evidence=["verified onchain"], wallet=WALLET))
