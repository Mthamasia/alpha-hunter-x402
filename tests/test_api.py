from app import SCHEMA_VERSION
from app.models import InvestigateResponse
from tests.conftest import USDC_MINT, WSOL_MINT

URL = "/v1/token/investigate"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"


def test_readiness_exposes_no_sensitive_configuration(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "provider": "stub"}


def test_cors_is_explicit_and_exposes_x402_headers():
    from app.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(
            Settings(
                cors_allow_origins=("https://app.example.test",),
            )
        )
    )
    preflight = client.options(
        URL,
        headers={
            "Origin": "https://app.example.test",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://app.example.test"
    response = client.get("/health", headers={"Origin": "https://app.example.test"})
    assert "PAYMENT-REQUIRED" in response.headers["access-control-expose-headers"]


def test_version(client):
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["provider"] == "stub"
    assert body["payment_mode"] == "disabled"


def test_valid_mint_matches_schema(client):
    r = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT})
    assert r.status_code == 200
    body = r.json()
    InvestigateResponse.model_validate(body)  # schema estrito
    assert set(body) == {
        "schema_version", "request_id", "chain", "mint", "status",
        "generated_at", "intelligence", "data_quality",
    }
    assert body["schema_version"] == "1.0"
    assert body["chain"] == "solana"
    assert body["mint"] == WSOL_MINT
    assert body["status"] == "NO_DATA_SOURCE"
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert body["generated_at"].endswith("Z")
    assert body["data_quality"]["limitations"]


def test_request_ids_are_unique(client):
    a = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}).json()
    b = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT}).json()
    assert a["request_id"] != b["request_id"]


def test_invalid_mints_rejected(client):
    bad = [
        "",
        "abc",
        "0OIl" * 10,                     # caracteres fora do base58
        WSOL_MINT + "x" * 20,           # muito longo
        "1" * 44,                       # base58 válido, mas não decodifica para 32 bytes
        "../../etc/passwd",             # caminho de arquivo
        "C:\\Windows\\system32\\cmd.exe",
        "$(rm -rf /)",
        " " + WSOL_MINT,
        123,
        None,
    ]
    for mint in bad:
        r = client.post(URL, json={"chain": "solana", "mint": mint})
        assert r.status_code in (400, 422), mint
        body = r.json()
        assert body["error"]["code"] == "validation_error"
        # Não ecoa o input do cliente
        if isinstance(mint, str) and mint:
            assert mint not in str(body["error"].get("details", []))


def test_invalid_chain_and_extra_fields_rejected(client):
    assert client.post(URL, json={"chain": "ethereum", "mint": WSOL_MINT}).status_code == 422
    r = client.post(URL, json={"chain": "solana", "mint": WSOL_MINT, "path": "/etc/passwd"})
    assert r.status_code == 422
    assert client.post(URL, json={"mint": WSOL_MINT}).status_code == 422


def test_body_too_large_rejected(client):
    r = client.post(URL, content=b'{"chain":"solana","mint":"' + b"a" * 5000 + b'"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_stub_never_fabricates_intelligence(client):
    for mint in (WSOL_MINT, USDC_MINT):
        body = client.post(URL, json={"chain": "solana", "mint": mint}).json()
        assert body["status"] == "NO_DATA_SOURCE"
        assert body["intelligence"] == {
            "creator": None,
            "wallet_activity": [],
            "concentration": None,
            "behavioral_findings": [],
            "evidence": [],
        }
        assert body["data_quality"]["status"] == "UNAVAILABLE"
        assert any("Alpha Hunter" in s for s in body["data_quality"]["limitations"])
