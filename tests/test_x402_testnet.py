"""PAYMENT_MODE=x402-testnet com a biblioteca oficial x402-avm.

Nenhum teste fala com o facilitator real nem com a blockchain: usamos um
facilitator falso que implementa o protocolo FacilitatorClient da biblioteca.
Nenhum fundo é movimentado e nenhuma chave privada é usada.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from x402.http import decode_payment_required_header, decode_payment_response_header
from x402.http.utils import encode_payment_signature_header
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2, TESTNET_GENESIS_HASH
from x402.schemas import (
    PaymentPayload,
    SettleResponse,
    SupportedKind,
    SupportedResponse,
    VerifyResponse,
)

from app.config import ConfigError, Settings
from app.main import create_app
from app.payments.x402_avm import build_facilitator_client
from tests.conftest import NetworkBlocked, WSOL_MINT

URL = "/v1/token/investigate"
REQ = {"chain": "solana", "mint": WSOL_MINT}
# Endereço público de recebimento (não é segredo).
RECEIVER = "A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ"
FACILITATOR = "https://facilitator.goplausible.xyz"
USDC_TESTNET = "10458941"
EXPECTED_CAIP2 = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="


class FakeFacilitator:
    """Facilitator em memória. Registra chamadas; nunca toca a rede."""

    def __init__(self, valid: bool = True, settle_ok: bool = True, supported: bool = True):
        self.valid = valid
        self.settle_ok = settle_ok
        self.supported = supported
        self.verify_calls = 0
        self.settle_calls = 0

    def get_supported(self) -> SupportedResponse:
        if not self.supported:
            raise ConnectionError("facilitator unreachable")
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=ALGORAND_TESTNET_CAIP2)]
        )

    async def verify(self, payload, requirements) -> VerifyResponse:
        self.verify_calls += 1
        if self.valid:
            return VerifyResponse(is_valid=True, payer="FAKEPAYER")
        return VerifyResponse(is_valid=False, invalid_reason="invalid_exact_avm_payload_invalid_signature")

    async def settle(self, payload, requirements) -> SettleResponse:
        self.settle_calls += 1
        return SettleResponse(
            success=self.settle_ok,
            error_reason=None if self.settle_ok else "settle_failed",
            transaction="FAKE-TX-ID" if self.settle_ok else "",
            network=ALGORAND_TESTNET_CAIP2,
        )


def x402_settings(**overrides) -> Settings:
    base = dict(payment_mode="x402-testnet", pay_to=RECEIVER, x402_facilitator_url=FACILITATOR)
    base.update(overrides)
    return Settings(**base)


def make_x402_client(facilitator=None, **overrides):
    facilitator = facilitator or FakeFacilitator()
    app = create_app(x402_settings(**overrides), facilitator_client=facilitator)
    return TestClient(app, raise_server_exceptions=False), facilitator


def get_requirements(client: TestClient):
    r = client.post(URL, json=REQ)
    assert r.status_code == 402
    return decode_payment_required_header(r.headers["PAYMENT-REQUIRED"])


def fake_signature_header(requirements) -> str:
    # Payload sintético: só o facilitator falso o "verifica". Não é uma transação assinada.
    payload = PaymentPayload(payload={"paymentGroup": [], "paymentIndex": 0}, accepted=requirements)
    return encode_payment_signature_header(payload)


# ---------------------------------------------------------------- 402 / requirements


def test_x402_without_payment_returns_402():
    client, fac = make_x402_client()
    r = client.post(URL, json=REQ)
    assert r.status_code == 402
    assert "PAYMENT-REQUIRED" in r.headers
    assert r.headers["X-Request-ID"]
    assert fac.verify_calls == fac.settle_calls == 0


def test_requirements_are_real_x402_v2():
    client, _ = make_x402_client()
    pr = get_requirements(client)
    assert pr.x402_version == 2
    assert pr.resource.url.endswith(URL)
    assert len(pr.accepts) == 1
    assert pr.accepts[0].scheme == "exact"
    assert pr.accepts[0].max_timeout_seconds == 300


def test_requirements_include_bazaar_discovery_metadata():
    client, _ = make_x402_client()
    metadata = get_requirements(client).extensions["bazaar"]["info"]
    assert metadata["input"]["type"] == "http"
    assert metadata["input"]["method"] == "POST"
    assert metadata["output"]["type"] == "json"
    assert "x402-global-challenge" in metadata["tags"]
    assert "mint" in metadata["input"]["body"]


def test_requirements_indicate_algorand_testnet():
    client, _ = make_x402_client()
    req = get_requirements(client).accepts[0]
    assert req.network == EXPECTED_CAIP2 == ALGORAND_TESTNET_CAIP2
    assert req.extra["genesisHash"] == TESTNET_GENESIS_HASH
    assert req.extra["genesisId"] == "testnet-v1.0"


def test_requirements_indicate_usdc_10458941():
    client, _ = make_x402_client()
    req = get_requirements(client).accepts[0]
    assert req.asset == USDC_TESTNET
    assert req.extra["decimals"] == 6


def test_requirements_indicate_amount_50000():
    client, _ = make_x402_client()
    assert get_requirements(client).accepts[0].amount == "50000"


def test_requirements_indicate_configured_receiver():
    client, _ = make_x402_client()
    assert get_requirements(client).accepts[0].pay_to == RECEIVER


def test_invalid_input_without_payment_still_402():
    # A middleware x402 roda antes da validação do body.
    client, _ = make_x402_client()
    assert client.post(URL, json={"chain": "solana", "mint": "bad"}).status_code == 402


def test_other_routes_are_not_paywalled():
    client, fac = make_x402_client()
    assert client.get("/health").status_code == 200
    assert client.get("/version").json()["payment_mode"] == "x402-testnet"
    assert fac.verify_calls == 0


def test_public_base_url_is_used_for_discovery_resource():
    client, _ = make_x402_client(public_base_url="https://api.example.test")
    assert get_requirements(client).resource.url == "https://api.example.test/v1/token/investigate"


# ---------------------------------------------------------------- verify / settle (fake)


def test_valid_payment_is_verified_and_settled_before_release():
    client, fac = make_x402_client()
    req = get_requirements(client).accepts[0]
    r = client.post(URL, json=REQ, headers={"PAYMENT-SIGNATURE": fake_signature_header(req)})
    assert r.status_code == 200
    assert r.json()["status"] == "NO_DATA_SOURCE"
    assert fac.verify_calls == 1 and fac.settle_calls == 1
    settle = decode_payment_response_header(r.headers["PAYMENT-RESPONSE"])
    assert settle.success and settle.transaction == "FAKE-TX-ID"


def test_invalid_payment_rejected_without_settle():
    client, fac = make_x402_client(FakeFacilitator(valid=False))
    req = get_requirements(client).accepts[0]
    r = client.post(URL, json=REQ, headers={"PAYMENT-SIGNATURE": fake_signature_header(req)})
    assert r.status_code == 402
    assert fac.verify_calls == 1 and fac.settle_calls == 0


def test_failed_settlement_does_not_release_resource():
    client, fac = make_x402_client(FakeFacilitator(settle_ok=False))
    req = get_requirements(client).accepts[0]
    r = client.post(URL, json=REQ, headers={"PAYMENT-SIGNATURE": fake_signature_header(req)})
    assert r.status_code == 402
    assert "NO_DATA_SOURCE" not in r.text
    assert fac.settle_calls == 1


def test_endpoint_error_is_not_settled():
    # Pagamento válido + input inválido -> 422 e nenhuma cobrança.
    client, fac = make_x402_client()
    req = get_requirements(client).accepts[0]
    r = client.post(URL, json={"chain": "solana", "mint": "bad"},
                    headers={"PAYMENT-SIGNATURE": fake_signature_header(req)})
    assert r.status_code == 422
    assert fac.settle_calls == 0


def test_mismatched_payment_requirements_rejected():
    client, fac = make_x402_client()
    req = get_requirements(client).accepts[0].model_copy(update={"amount": "1"})
    r = client.post(URL, json=REQ, headers={"PAYMENT-SIGNATURE": fake_signature_header(req)})
    assert r.status_code == 402
    assert fac.verify_calls == 0 and fac.settle_calls == 0


def test_garbage_payment_header_rejected():
    client, fac = make_x402_client()
    r = client.post(URL, json=REQ, headers={"PAYMENT-SIGNATURE": "not-base64!!"})
    assert r.status_code == 402
    assert fac.verify_calls == 0


def test_unreachable_facilitator_fails_closed():
    client, _ = make_x402_client(FakeFacilitator(supported=False))
    r = client.post(URL, json=REQ)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "payment_unavailable"
    assert "NO_DATA_SOURCE" not in r.text


def test_real_facilitator_client_never_reaches_network():
    # Com o cliente HTTP real, a fixture de rede bloqueia o acesso -> 503 (fail closed).
    app = create_app(x402_settings())
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(URL, json=REQ).status_code == 503
    fac = build_facilitator_client(x402_settings())
    assert fac.url == FACILITATOR
    with pytest.raises(NetworkBlocked):
        fac.get_supported()


# ---------------------------------------------------------------- modos existentes


def test_disabled_mode_still_works(client):
    r = client.post(URL, json=REQ)
    assert r.status_code == 200
    assert "PAYMENT-REQUIRED" not in r.headers


def test_simulated_test_mode_still_works(paid_client):
    from app.payments.gate import build_test_payment_header

    assert paid_client.post(URL, json=REQ).status_code == 402
    ok = paid_client.post(URL, json=REQ, headers={"X-PAYMENT": build_test_payment_header("testnet")})
    assert ok.status_code == 200


# ---------------------------------------------------------------- configuração


@pytest.mark.parametrize(
    "overrides",
    [
        {"pay_to": ""},
        {"pay_to": "not-an-address"},
        {"pay_to": RECEIVER[:-1] + ("A" if RECEIVER[-1] != "A" else "B")},  # checksum inválido
        {"x402_network": "algorand-mainnet"},
        {"x402_network": "mainnet"},
        {"x402_network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="},  # genesis mainnet
        {"x402_network": "solana-devnet"},
        {"x402_facilitator_url": "http://facilitator.goplausible.xyz"},
        {"x402_facilitator_url": "https://user:pass@facilitator.goplausible.xyz"},
        {"x402_facilitator_url": "not a url"},
        {"price_usd": Decimal("0.0000001")},
        {"price_usd": Decimal("0")},
        {"payment_mode": "x402-mainnet"},
    ],
    ids=["no-payto", "bad-payto", "bad-checksum", "mainnet-alias", "mainnet", "mainnet-caip2",
         "wrong-chain", "http-url", "url-creds", "bad-url", "too-precise", "zero-price", "x402-mainnet"],
)
def test_invalid_config_fails_safely(overrides):
    with pytest.raises(ConfigError):
        x402_settings(**overrides)


def test_testnet_network_aliases_accepted():
    for net in ("testnet", "algorand-testnet", EXPECTED_CAIP2):
        assert x402_settings(x402_network=net).price_atomic == 50000


def test_disabled_mode_does_not_require_pay_to():
    assert Settings(payment_mode="disabled").pay_to == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"app_env": "production"},
        {
            "app_env": "production",
            "public_base_url": "http://api.example.test",
            "cors_allow_origins": ("https://app.example.test",),
        },
        {
            "app_env": "production",
            "public_base_url": "https://api.example.test",
        },
        {
            "app_env": "production",
            "public_base_url": "https://api.example.test",
            "cors_allow_origins": ("https://app.example.test",),
            "intelligence_provider": "alpha_hunter",
            "ahx_bridge_service_token": "test-token",
        },
    ],
)
def test_production_configuration_fails_closed(overrides):
    with pytest.raises(ConfigError):
        Settings(**overrides)


# ---------------------------------------------------------------- sem chaves / sem segredos


def test_no_private_key_or_signer_in_server_code():
    root = Path(__file__).resolve().parent.parent
    forbidden = ("mnemonic", "private_key", "privatekey", "secret_key", "ClientAvmSigner",
                 "FacilitatorAvmSigner", "register_exact_avm_client", "to_private_key")
    for path in (root / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert word.lower() not in text, (path, word)
    fields = set(Settings.__dataclass_fields__)
    assert not any(k in f for f in fields for k in ("key", "secret", "mnemonic", "seed"))


def test_env_example_has_no_secrets_and_empty_pay_to():
    root = Path(__file__).resolve().parent.parent
    env = dict(
        line.split("=", 1)
        for line in (root / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert env["PAY_TO"] == ""
    assert env["PAYMENT_MODE"] == "disabled"
    assert env["X402_FACILITATOR_URL"] == FACILITATOR
    assert not any(k in name.lower() for name in env for k in ("key", "secret", "mnemonic", "seed"))
