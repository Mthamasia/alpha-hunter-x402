import logging
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.main import _billable_intelligence, create_app
from app.providers import AlphaHunterProvider, StubIntelligenceProvider
from tests.conftest import WSOL_MINT
from tests.evidence_helpers import tx_evidence

URL = "/v1/token/investigate"
SERVICE_TOKEN = "test-service-token-must-not-leak"


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
        "creator": {
            "address": "creator-address",
            "profile": {
                "trust_score": 100.0,
            },
        },
        "wallet_activity": {
            "market": {
                "marketcap": 1000000.0,
                "liquidity": 100000.0,
                "volume": 50000.0,
            },
            "profile": {
                "total_events": 10,
            },
            "recent_events": [],
            "related_wallets": [],
        },
        "behavioral_findings": {
            "scores": {
                "alpha_score": 89.0,
                "risk_score": 25.0,
            },
            "decision": "WAIT",
            "flags": {
                "rugged": False,
                "graduated": False,
                "listed": False,
            },
            "alerts": [],
            "explanation": None,
        },
        "concentration": {
            "top_10_percent": 42,
        },
        "evidence": [
            {
                "source": "read-only-bridge",
            }
        ],
        "data_quality": {
            "status": "FRESH",
            "limitations": [
                "Bridge is read-only.",
            ],
        },
    }
    payload.update(overrides)
    return payload


def alpha_client(monkeypatch, handler):
    def get(self, url, *, headers):
        return handler(url, headers)

    monkeypatch.setattr(httpx.Client, "get", get)

    settings = Settings(
        payment_mode="disabled",
        intelligence_provider="alpha_hunter",
        ahx_bridge_service_token=SERVICE_TOKEN,
    )

    return create_app(settings)


def test_alpha_hunter_selected_by_configuration(monkeypatch):
    app = alpha_client(
        monkeypatch,
        lambda _url, _headers: httpx.Response(
            200,
            json=bridge_payload(),
        ),
    )

    assert isinstance(app.state.provider, AlphaHunterProvider)
    assert app.state.provider.name == "alpha_hunter"


def test_stub_remains_available():
    app = create_app(
        Settings(
            payment_mode="disabled",
            intelligence_provider="stub",
        )
    )

    assert isinstance(app.state.provider, StubIntelligenceProvider)


def test_alpha_hunter_maps_valid_bridge_response(monkeypatch):
    captured = {}

    def handler(url, headers):
        captured.update(
            url=url,
            headers=headers,
        )

        return httpx.Response(
            200,
            json=bridge_payload(),
        )

    client = TestClient(
        alpha_client(
            monkeypatch,
            handler,
        )
    )

    response = client.post(
        URL,
        json={
            "chain": "solana",
            "mint": WSOL_MINT,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert (
        captured["url"]
        == f"http://127.0.0.1:8787/v1/x402/token/{WSOL_MINT}"
    )

    assert captured["headers"] == {
        "X-AHX-Bridge-Token": SERVICE_TOKEN
    }

    assert body["status"] == "FRESH"

    # A trust score with no observed launch outcomes is not reputation:
    # the public response reports UNKNOWN and says why.
    assert body["intelligence"]["creator"] == {
        "address": "creator-address",
        "profile": {},
        "reputation": "UNKNOWN",
    }

    assert body["intelligence"]["wallet_activity"]["market"][
        "marketcap"
    ] == 1000000.0

    assert (
        body["intelligence"]["behavioral_findings"]["decision"]
        == "WAIT"
    )

    assert body["intelligence"]["concentration"] == {
        "top_10_percent": 42
    }

    assert body["intelligence"]["evidence"] == [
        {
            "source": "read-only-bridge",
        }
    ]

    assert body["data_quality"] == {
        "status": "FRESH",
        "limitations": [
            "Bridge is read-only.",
            "Creator reputation reported as UNKNOWN: no observed launch outcomes support a score.",
        ],
    }


def test_alpha_hunter_preserves_null_concentration_empty_evidence_and_stale(
    monkeypatch,
):
    payload = bridge_payload(
        concentration=None,
        evidence=[],
        freshness={
            "market_data_updated_at": "2026-08-23T22:28:49Z",
            "age_seconds": 2745044,
            "stale_after_seconds": 3600,
            "is_stale": True,
            "last_activity_at": "2026-08-23T22:28:49Z",
        },
        data_quality={
            "status": "STALE",
            "limitations": [
                "Dados de mercado antigos: STALE.",
            ],
        },
    )

    client = TestClient(
        alpha_client(
            monkeypatch,
            lambda _url, _headers: httpx.Response(
                200,
                json=payload,
            ),
        )
    )

    response = client.post(
        URL,
        json={
            "chain": "solana",
            "mint": WSOL_MINT,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "STALE"
    assert body["intelligence"]["concentration"] is None
    assert body["intelligence"]["evidence"] == []
    assert body["data_quality"]["status"] == "STALE"

    assert body["data_quality"]["limitations"] == [
        "Dados de mercado antigos: STALE.",
        "Creator reputation reported as UNKNOWN: no observed launch outcomes support a score.",
    ]


@pytest.mark.parametrize(
    (
        "status_code",
        "expected_status",
        "expected_code",
    ),
    [
        (
            401,
            500,
            "provider_configuration_error",
        ),
        (
            403,
            500,
            "provider_configuration_error",
        ),
        (
            500,
            503,
            "provider_unavailable",
        ),
    ],
)
def test_alpha_hunter_http_errors_are_deterministic(
    monkeypatch,
    status_code,
    expected_status,
    expected_code,
):
    client = TestClient(
        alpha_client(
            monkeypatch,
            lambda _url, _headers: httpx.Response(
                status_code
            ),
        )
    )

    response = client.post(
        URL,
        json={
            "chain": "solana",
            "mint": WSOL_MINT,
        },
    )

    assert response.status_code == expected_status

    assert (
        response.json()["error"]["code"]
        == expected_code
    )

    assert SERVICE_TOKEN not in response.text


def test_alpha_hunter_404_returns_no_data_without_intelligence(
    monkeypatch,
):
    client = TestClient(
        alpha_client(
            monkeypatch,
            lambda _url, _headers: httpx.Response(404),
        )
    )

    response = client.post(
        URL,
        json={
            "chain": "solana",
            "mint": WSOL_MINT,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NO_DATA"

    assert response.json()["intelligence"] == {
        "creator": None,
        "wallet_activity": [],
        "concentration": None,
        "behavioral_findings": [],
        "evidence": [],
    }


@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("timeout"),
        httpx.ConnectError("connection refused"),
    ],
)
def test_alpha_hunter_transport_failures_are_unavailable(
    monkeypatch,
    error,
):
    def handler(_url, _headers):
        raise error

    client = TestClient(
        alpha_client(
            monkeypatch,
            handler,
        )
    )

    response = client.post(
        URL,
        json={
            "chain": "solana",
            "mint": WSOL_MINT,
        },
    )

    assert response.status_code == 503

    assert (
        response.json()["error"]["code"]
        == "provider_unavailable"
    )


@pytest.mark.parametrize(
    "payload",
    [
        bridge_payload(
            schema_version="2.0"
        ),
        bridge_payload(
            mint=(
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4w"
                "EGGkZwyTDt1v"
            )
        ),
    ],
)
def test_alpha_hunter_rejects_incompatible_bridge_payload(
    monkeypatch,
    payload,
):
    client = TestClient(
        alpha_client(
            monkeypatch,
            lambda _url, _headers: httpx.Response(
                200,
                json=payload,
            ),
        )
    )

    response = client.post(
        URL,
        json={
            "chain": "solana",
            "mint": WSOL_MINT,
        },
    )

    assert response.status_code == 502

    assert (
        response.json()["error"]["code"]
        == "provider_error"
    )


def test_alpha_hunter_token_is_not_exposed_in_errors_or_logs(
    monkeypatch,
    caplog,
):
    caplog.set_level(
        logging.ERROR,
        logger="ahx",
    )

    client = TestClient(
        alpha_client(
            monkeypatch,
            lambda _url, _headers: httpx.Response(401),
        )
    )

    response = client.post(
        URL,
        json={
            "chain": "solana",
            "mint": WSOL_MINT,
        },
    )

    assert SERVICE_TOKEN not in response.text
    assert SERVICE_TOKEN not in caplog.text


def test_alpha_hunter_never_accesses_sqlite(monkeypatch):
    def fail_sqlite(*_args, **_kwargs):
        raise AssertionError(
            "sqlite must not be accessed by the HTTP provider"
        )

    monkeypatch.setattr(
        sqlite3,
        "connect",
        fail_sqlite,
    )

    provider = AlphaHunterProvider(
        Settings(
            intelligence_provider="alpha_hunter",
            ahx_bridge_service_token=SERVICE_TOKEN,
        )
    )

    mapped = provider._map_response(
        bridge_payload(),
        WSOL_MINT,
    )

    assert mapped.status == "FRESH"


def test_alpha_hunter_configuration_requires_service_token():
    with pytest.raises(
        ConfigError,
        match="AHX_BRIDGE_SERVICE_TOKEN",
    ):
        Settings(
            intelligence_provider="alpha_hunter"
        )

# Payment-eligibility safety check against inconsistent bridge freshness.
# Only in-memory payloads; these tests do not invoke Helius, x402, or HTTP.
@pytest.mark.parametrize(
    ("freshness", "expected_status"),
    [
        ({"is_stale": True, "age_seconds": 2, "stale_after_seconds": 60}, "STALE"),
        ({"is_stale": False, "age_seconds": 60, "stale_after_seconds": 60}, "STALE"),
        ({"is_stale": False, "age_seconds": 61, "stale_after_seconds": 60}, "STALE"),
        ({"is_stale": None, "age_seconds": 2, "stale_after_seconds": 60}, "UNAVAILABLE"),
        ({"is_stale": False, "age_seconds": -1, "stale_after_seconds": 60}, "UNAVAILABLE"),
        ({"is_stale": False, "age_seconds": 0, "stale_after_seconds": 0}, "UNAVAILABLE"),
        ({"is_stale": False, "age_seconds": True, "stale_after_seconds": 60}, "UNAVAILABLE"),
        ({"is_stale": False, "age_seconds": 2, "stale_after_seconds": False}, "UNAVAILABLE"),
        ({"is_stale": False, "age_seconds": float("nan"), "stale_after_seconds": 60}, "UNAVAILABLE"),
        ({"is_stale": False, "age_seconds": 2, "stale_after_seconds": float("inf")}, "UNAVAILABLE"),
        ({}, "UNAVAILABLE"),
    ],
)
def test_false_fresh_cannot_be_billed(freshness, expected_status):
    payload = bridge_payload(
        freshness=freshness,
        creator={"profile": {"verified_launches": 2}},
        evidence=[tx_evidence(WSOL_MINT)],
        data_quality={"status": "FRESH", "limitations": []},
    )
    result = AlphaHunterProvider._map_response(payload, WSOL_MINT)
    assert result.data_quality.status == expected_status
    assert result.status == expected_status
    assert result.data_quality.limitations
    assert not _billable_intelligence(result, WSOL_MINT, max_evidence_age_seconds=86400)


def test_consistent_freshness_with_traceable_behavior_remains_eligible():
    payload = bridge_payload(
        freshness={
            "is_stale": False,
            "age_seconds": 10,
            "stale_after_seconds": 60,
        },
        creator={"profile": {"verified_launches": 2}},
        evidence=[tx_evidence(WSOL_MINT)],
        data_quality={"status": "FRESH", "limitations": []},
    )
    result = AlphaHunterProvider._map_response(payload, WSOL_MINT)
    assert result.data_quality.status == "FRESH"
    assert result.status == "FRESH"
    assert _billable_intelligence(result, WSOL_MINT, max_evidence_age_seconds=86400)
