"""PaymentGate: camada de pagamento desacoplada, inspirada no fluxo HTTP 402 do x402.

Modos:
- disabled: toda requisição passa.
- test: simula SEMANTICAMENTE o fluxo 402. Não há blockchain, facilitator,
  assinatura, transação ou fundos. NÃO é x402 real.
- x402-testnet: x402 V2 real, tratado pela middleware oficial (x402_avm.py);
  aqui é apenas pass-through.

Header de pagamento de teste (modo test):
    X-PAYMENT: base64( JSON {"x402Version": 1, "scheme": "test",
                              "network": <X402_NETWORK>, "payload": {"test": true}} )
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from app.config import Settings

PAYMENT_HEADER = "X-PAYMENT"
PAYMENT_RESPONSE_HEADER = "X-PAYMENT-RESPONSE"
X402_VERSION = 1
TEST_SCHEME = "test"
MAX_PAYMENT_HEADER_LEN = 2048


class PaymentRequired(Exception):
    def __init__(self, body: dict[str, Any]):
        super().__init__(body.get("error", "payment required"))
        self.body = body


@dataclass(frozen=True)
class PaymentDecision:
    paid: bool
    simulated: bool
    response_header: str | None = None


def build_test_payment_header(network: str) -> str:
    """Gera um header X-PAYMENT de TESTE válido (útil para testes e curl)."""
    payload = {
        "x402Version": X402_VERSION,
        "scheme": TEST_SCHEME,
        "network": network,
        "payload": {"test": True},
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


class PaymentGate:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def mode(self) -> str:
        return self.settings.payment_mode

    def requirements(self, resource: str) -> dict[str, Any]:
        return {
            "scheme": TEST_SCHEME,
            "network": self.settings.x402_network,
            "price_usd": str(self.settings.price_usd),
            "resource": resource,
            "description": "On-chain token investigation (SIMULATED payment, test mode)",
            "mimeType": "application/json",
            "payTo": self.settings.pay_to,
        }

    def _required(self, resource: str, error: str) -> PaymentRequired:
        return PaymentRequired(
            {
                "x402Version": X402_VERSION,
                "simulated": True,
                "error": error,
                "accepts": [self.requirements(resource)],
            }
        )

    def verify(self, header_value: str | None, resource: str) -> PaymentDecision:
        if self.mode == "disabled":
            return PaymentDecision(paid=False, simulated=False)
        if self.mode in ("x402-testnet", "x402-mainnet"):
            # Verify/settle feitos pela middleware oficial x402 (app/payments/x402_avm.py)
            # antes de a requisição chegar aqui.
            return PaymentDecision(paid=True, simulated=False)

        # mode == "test"
        if not header_value:
            raise self._required(resource, f"{PAYMENT_HEADER} header is required")
        if len(header_value) > MAX_PAYMENT_HEADER_LEN:
            raise self._required(resource, f"{PAYMENT_HEADER} header too large")
        try:
            data = json.loads(base64.b64decode(header_value, validate=True))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise self._required(resource, f"{PAYMENT_HEADER} header is malformed")

        if not (
            isinstance(data, dict)
            and data.get("x402Version") == X402_VERSION
            and data.get("scheme") == TEST_SCHEME
            and data.get("network") == self.settings.x402_network
            and isinstance(data.get("payload"), dict)
            and data["payload"].get("test") is True
        ):
            raise self._required(resource, "invalid test payment")

        receipt = {"success": True, "simulated": True, "network": self.settings.x402_network}
        return PaymentDecision(
            paid=True,
            simulated=True,
            response_header=base64.b64encode(json.dumps(receipt).encode()).decode(),
        )
