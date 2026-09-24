"""x402 V2 real em Algorand Testnet (PAYMENT_MODE=x402-testnet).

Usa exclusivamente a biblioteca oficial `x402-avm`: o protocolo (402 com o header
PAYMENT-REQUIRED, leitura de PAYMENT-SIGNATURE, verify e settle via facilitator)
é todo dela. Aqui só montamos a configuração da rota.

O servidor nunca assina nada nem guarda chave: quem assina é o cliente pagador,
e quem submete a transação é o facilitator.
"""

from __future__ import annotations

from typing import Any

from x402 import x402ResourceServer
from x402.http import FacilitatorConfig, HTTPFacilitatorClient
from x402.http.middleware.fastapi import payment_middleware
from x402.mechanisms.avm.exact.register import register_exact_avm_server

from app.config import USDC_DECIMALS, Settings
from app.discovery import bazaar_extension

INVESTIGATE_ROUTE = "POST /v1/token/investigate"
FACILITATOR_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 300


def build_routes(settings: Settings) -> dict[str, Any]:
    return {
        INVESTIGATE_ROUTE: {
            **(
                {"resource": f"{settings.public_base_url}/v1/token/investigate"}
                if settings.public_base_url
                else {}
            ),
            "accepts": {
                "scheme": "exact",
                "network": settings.x402_caip2,
                "payTo": settings.pay_to,
                # AssetAmount explícito (unidades mínimas) para não depender de
                # conversão via float no parser padrão de preço.
                "price": {
                    "amount": str(settings.price_atomic),
                    "asset": str(settings.x402_usdc_asa_id),
                    "extra": {"decimals": USDC_DECIMALS},
                },
                "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
            },
            "description": "AHX on-chain token investigation",
            "mimeType": "application/json",
            "extensions": bazaar_extension(settings),
        }
    }


def build_facilitator_client(settings: Settings) -> HTTPFacilitatorClient:
    return HTTPFacilitatorClient(
        FacilitatorConfig(url=settings.x402_facilitator_url, timeout=FACILITATOR_TIMEOUT_SECONDS)
    )


def build_x402_middleware(settings: Settings, facilitator_client: Any = None):
    """Retorna a middleware FastAPI oficial do x402, configurada para Algorand Testnet.

    `facilitator_client` permite injetar um facilitator falso nos testes.
    """
    server = x402ResourceServer(facilitator_client or build_facilitator_client(settings))
    register_exact_avm_server(server, settings.x402_caip2)
    return payment_middleware(build_routes(settings), server)
