"""Configuração carregada de variáveis de ambiente (.env opcional).

Nenhum valor aqui é segredo. Nunca coloque seed phrase / private key em .env.
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from dotenv import load_dotenv

PAYMENT_MODES = ("disabled", "test", "x402-testnet")
INTELLIGENCE_PROVIDERS = ("stub", "alpha_hunter")
APP_ENVS = ("development", "test", "production")
DEFAULT_FACILITATOR_URL = "https://facilitator.goplausible.xyz"
DEFAULT_AHX_BRIDGE_URL = "http://127.0.0.1:8787"
DEFAULT_AHX_BRIDGE_TIMEOUT_SECONDS = 3.0
# Aliases aceitos em X402_NETWORK para Algorand Testnet (modo x402-testnet).
ALGORAND_TESTNET_ALIASES = ("testnet", "algorand-testnet")
USDC_DECIMALS = 6


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    payment_mode: str = "disabled"
    price_usd: Decimal = Decimal("0.05")
    pay_to: str = ""
    x402_network: str = "testnet"
    x402_facilitator_url: str = DEFAULT_FACILITATOR_URL
    intelligence_provider: str = "stub"
    ahx_bridge_url: str = DEFAULT_AHX_BRIDGE_URL
    ahx_bridge_service_token: str = ""
    ahx_bridge_timeout_seconds: float = DEFAULT_AHX_BRIDGE_TIMEOUT_SECONDS
    public_base_url: str = ""
    cors_allow_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.app_env not in APP_ENVS:
            raise ConfigError(f"APP_ENV inválido: {self.app_env!r}. Use um de {APP_ENVS}.")
        if self.payment_mode not in PAYMENT_MODES:
            raise ConfigError(
                f"PAYMENT_MODE inválido: {self.payment_mode!r}. Use um de {PAYMENT_MODES}."
            )
        if self.price_usd <= 0:
            raise ConfigError("PRICE_USD deve ser maior que zero.")
        if self.intelligence_provider not in INTELLIGENCE_PROVIDERS:
            raise ConfigError(
                "INTELLIGENCE_PROVIDER inválido: "
                f"{self.intelligence_provider!r}. Use um de {INTELLIGENCE_PROVIDERS}."
            )
        if not math.isfinite(self.ahx_bridge_timeout_seconds) or self.ahx_bridge_timeout_seconds <= 0:
            raise ConfigError("AHX_BRIDGE_TIMEOUT_SECONDS deve ser maior que zero.")
        if self.intelligence_provider == "alpha_hunter":
            self._validate_ahx_bridge()
        self._validate_public_base_url()
        self._validate_cors_origins()
        if self.app_env == "production":
            self._validate_production()
        # Nesta fase Mainnet é proibida.
        if "mainnet" in self.x402_network.lower():
            raise ConfigError("X402_NETWORK=mainnet não é permitido nesta fase.")
        if self.payment_mode == "x402-testnet":
            self._validate_x402_testnet()

    def _validate_x402_testnet(self) -> None:
        from algosdk.encoding import is_valid_address
        from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2

        if not self.pay_to:
            raise ConfigError("PAY_TO é obrigatório em PAYMENT_MODE=x402-testnet.")
        if not is_valid_address(self.pay_to):
            raise ConfigError("PAY_TO não é um endereço Algorand válido.")
        if self.x402_network not in (*ALGORAND_TESTNET_ALIASES, ALGORAND_TESTNET_CAIP2):
            raise ConfigError("X402_NETWORK deve indicar Algorand Testnet em x402-testnet.")
        url = urlparse(self.x402_facilitator_url)
        if url.scheme != "https" or not url.hostname:
            raise ConfigError("X402_FACILITATOR_URL deve ser uma URL https válida.")
        if url.username or url.password:
            raise ConfigError("X402_FACILITATOR_URL não pode conter credenciais.")
        self.price_atomic  # valida que o preço é representável em USDC

    def _validate_ahx_bridge(self) -> None:
        if not self.ahx_bridge_service_token:
            raise ConfigError(
                "AHX_BRIDGE_SERVICE_TOKEN é obrigatório em INTELLIGENCE_PROVIDER=alpha_hunter."
            )
        url = urlparse(self.ahx_bridge_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ConfigError("AHX_BRIDGE_URL deve ser uma URL http(s) válida.")
        if url.username or url.password:
            raise ConfigError("AHX_BRIDGE_URL não pode conter credenciais.")

    def _validate_public_base_url(self) -> None:
        if not self.public_base_url:
            return
        url = urlparse(self.public_base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ConfigError("PUBLIC_BASE_URL deve ser uma URL https válida sem credenciais.")
        if url.query or url.fragment:
            raise ConfigError("PUBLIC_BASE_URL não pode incluir query ou fragment.")

    def _validate_cors_origins(self) -> None:
        for origin in self.cors_allow_origins:
            url = urlparse(origin)
            if url.scheme != "https" or not url.hostname or url.path not in ("", "/"):
                raise ConfigError("CORS_ALLOW_ORIGINS aceita somente origins https válidas.")

    def _validate_production(self) -> None:
        if not self.public_base_url:
            raise ConfigError("PUBLIC_BASE_URL é obrigatório em APP_ENV=production.")
        if not self.cors_allow_origins:
            raise ConfigError("CORS_ALLOW_ORIGINS é obrigatório em APP_ENV=production.")
        bridge = urlparse(self.ahx_bridge_url)
        if bridge.hostname in ("localhost", "127.0.0.1", "::1"):
            raise ConfigError("AHX_BRIDGE_URL não pode apontar para localhost em produção.")
        if self.intelligence_provider == "alpha_hunter" and bridge.scheme != "https":
            raise ConfigError("AHX_BRIDGE_URL deve usar https em produção.")

    @property
    def price_atomic(self) -> int:
        """PRICE_USD em unidades mínimas de USDC (6 casas), sem arredondamento."""
        atomic = self.price_usd * (10**USDC_DECIMALS)
        if atomic != atomic.to_integral_value():
            raise ConfigError("PRICE_USD tem mais de 6 casas decimais.")
        return int(atomic)


def load_settings(env_file: str | None = ".env") -> Settings:
    if env_file:
        load_dotenv(env_file, override=False)
    raw_price = os.getenv("PRICE_USD", "0.05").strip()
    try:
        price = Decimal(raw_price)
    except InvalidOperation as exc:
        raise ConfigError("PRICE_USD deve ser numérico.") from exc
    raw_timeout = os.getenv(
        "AHX_BRIDGE_TIMEOUT_SECONDS", str(DEFAULT_AHX_BRIDGE_TIMEOUT_SECONDS)
    ).strip()
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise ConfigError("AHX_BRIDGE_TIMEOUT_SECONDS deve ser numérico.") from exc
    cors_origins = tuple(
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
        if origin.strip()
    )
    return Settings(
        app_env=os.getenv("APP_ENV", "development").strip().lower(),
        payment_mode=os.getenv("PAYMENT_MODE", "disabled").strip().lower(),
        price_usd=price,
        pay_to=os.getenv("PAY_TO", "").strip(),
        x402_network=os.getenv("X402_NETWORK", "testnet").strip(),
        x402_facilitator_url=(
            os.getenv("X402_FACILITATOR_URL", "").strip() or DEFAULT_FACILITATOR_URL
        ),
        intelligence_provider=os.getenv("INTELLIGENCE_PROVIDER", "stub").strip().lower(),
        ahx_bridge_url=(
            os.getenv("AHX_BRIDGE_URL", "").strip() or DEFAULT_AHX_BRIDGE_URL
        ),
        ahx_bridge_service_token=os.getenv("AHX_BRIDGE_SERVICE_TOKEN", ""),
        ahx_bridge_timeout_seconds=timeout,
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
        cors_allow_origins=cors_origins,
    )
