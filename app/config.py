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

PAYMENT_MODES = ("disabled", "test", "x402-testnet", "x402-mainnet")
INTELLIGENCE_PROVIDERS = ("stub", "alpha_hunter")
APP_ENVS = ("development", "test", "production")
DEFAULT_FACILITATOR_URL = "https://facilitator.goplausible.xyz"
DEFAULT_AHX_BRIDGE_URL = "http://127.0.0.1:8787"
DEFAULT_AHX_BRIDGE_TIMEOUT_SECONDS = 3.0
DEFAULT_PREVIEW_CACHE_TTL_SECONDS = 60.0
DEFAULT_PREVIEW_RATE_LIMIT_PER_MINUTE = 30
DEFAULT_HELIUS_CREDIT_COST_USD = None  # None = not configured; never silently priced as 0
# ":memory:" = telemetry is not persisted unless the operator explicitly
# sets TELEMETRY_DB_PATH. Safer default for this stage: no new on-disk
# footprint until someone opts in.
DEFAULT_TELEMETRY_DB_PATH = ":memory:"
# Aliases aceitos somente nos perfis x402 correspondentes.
ALGORAND_TESTNET_ALIASES = ("testnet", "algorand-testnet")
ALGORAND_MAINNET_ALIASES = ("mainnet", "algorand-mainnet")
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
    enable_mainnet_x402: bool = False
    preview_cache_ttl_seconds: float = DEFAULT_PREVIEW_CACHE_TTL_SECONDS
    preview_rate_limit_per_minute: int = DEFAULT_PREVIEW_RATE_LIMIT_PER_MINUTE
    # Cost/telemetry (app/telemetry.py). None = not configured = no cap
    # enforced yet; this stage only prepares the instrumentation.
    helius_daily_budget_credits: int | None = None
    helius_per_query_credit_limit: int | None = None
    # None = no verified $/credit rate configured. A positive confirmed/
    # estimated credit count with this still None must price as unknown,
    # never as 0 -- see app/telemetry.py:build_record.
    helius_credit_cost_usd: Decimal | None = DEFAULT_HELIUS_CREDIT_COST_USD
    telemetry_db_path: str = DEFAULT_TELEMETRY_DB_PATH
    # Persistent preview quota + API keys (app/quota.py). ":memory:" by
    # default for the same reason as telemetry: no new on-disk footprint
    # until an operator opts in. In production this MUST be a real,
    # persistent path (a Railway volume or equivalent) -- see README.
    quota_db_path: str = DEFAULT_TELEMETRY_DB_PATH
    # None = trust nothing but the raw transport-level peer address.
    # Setting this to e.g. "X-Forwarded-For" is only safe once Railway's
    # actual edge/proxy behavior for this exact header is confirmed.
    trusted_proxy_header: str | None = None
    # Only honor trusted_proxy_header when the immediate TCP peer's address
    # falls inside one of these CIDR blocks -- a configured header NAME
    # alone does not stop a client from sending it itself. Empty by
    # default: TRUSTED_PROXY_HEADER has no effect until this is also set.
    trusted_proxy_cidrs: tuple[str, ...] = ()
    # HMAC secret for pseudonymizing client IPs before they become a quota
    # subject_id. Strongly recommended in production; falls back to
    # unsalted SHA-256 (still not the raw IP at rest, but guessable) if
    # unset -- see app/quota.py:hash_identifier.
    quota_ip_pseudonymization_salt: str | None = None
    # Retention: how long telemetry rows are kept before periodic purge.
    # Quota buckets have no separate setting -- they are purged down to
    # "only the current minute/day bucket" (see QuotaStore.purge_expired),
    # since a past bucket has zero future enforcement value.
    telemetry_retention_days: int = 90
    retention_purge_interval_seconds: int = 3600
    quota_anonymous_per_minute: int = 10
    quota_anonymous_per_day: int = 50
    quota_anonymous_distinct_mints_per_day: int = 15
    quota_registered_per_minute: int = 30
    quota_registered_per_day: int = 300
    quota_registered_distinct_mints_per_day: int = 60

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
        if not math.isfinite(self.preview_cache_ttl_seconds) or self.preview_cache_ttl_seconds < 0:
            raise ConfigError("PREVIEW_CACHE_TTL_SECONDS deve ser >= 0.")
        if self.preview_rate_limit_per_minute <= 0:
            raise ConfigError("PREVIEW_RATE_LIMIT_PER_MINUTE deve ser maior que zero.")
        if self.helius_daily_budget_credits is not None and self.helius_daily_budget_credits < 0:
            raise ConfigError("HELIUS_DAILY_BUDGET_CREDITS não pode ser negativo.")
        if self.helius_per_query_credit_limit is not None and self.helius_per_query_credit_limit < 0:
            raise ConfigError("HELIUS_PER_QUERY_CREDIT_LIMIT não pode ser negativo.")
        if self.helius_credit_cost_usd is not None and self.helius_credit_cost_usd < 0:
            raise ConfigError("HELIUS_CREDIT_COST_USD não pode ser negativo.")
        for name in (
            "quota_anonymous_per_minute", "quota_anonymous_per_day", "quota_anonymous_distinct_mints_per_day",
            "quota_registered_per_minute", "quota_registered_per_day", "quota_registered_distinct_mints_per_day",
            "telemetry_retention_days", "retention_purge_interval_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name.upper()} deve ser maior que zero.")
        if self.trusted_proxy_header and not self.trusted_proxy_cidrs:
            raise ConfigError(
                "TRUSTED_PROXY_HEADER exige TRUSTED_PROXY_CIDRS: um nome de header configurado, "
                "sem restringir de qual peer ele é aceito, não impede spoofing."
            )
        import ipaddress as _ipaddress
        for cidr in self.trusted_proxy_cidrs:
            try:
                _ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ConfigError(f"TRUSTED_PROXY_CIDRS contém um bloco inválido: {cidr!r}.") from exc
        if self.intelligence_provider == "alpha_hunter":
            self._validate_ahx_bridge()
        self._validate_public_base_url()
        self._validate_cors_origins()
        if self.app_env == "production":
            self._validate_production()
        if self.payment_mode != "x402-mainnet" and "mainnet" in self.x402_network.lower():
            raise ConfigError("X402_NETWORK=mainnet exige PAYMENT_MODE=x402-mainnet.")
        if self.payment_mode == "x402-testnet":
            self._validate_x402_testnet()
        if self.payment_mode == "x402-mainnet":
            self._validate_x402_mainnet()

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

    def _validate_x402_mainnet(self) -> None:
        from algosdk.encoding import is_valid_address
        from x402.mechanisms.avm import ALGORAND_MAINNET_CAIP2

        if not self.enable_mainnet_x402:
            raise ConfigError(
                "ENABLE_MAINNET_X402=true é obrigatório em PAYMENT_MODE=x402-mainnet."
            )
        if self.app_env != "production":
            raise ConfigError("PAYMENT_MODE=x402-mainnet exige APP_ENV=production.")
        if self.intelligence_provider != "alpha_hunter":
            raise ConfigError("PAYMENT_MODE=x402-mainnet exige INTELLIGENCE_PROVIDER=alpha_hunter.")
        if not self.pay_to:
            raise ConfigError("PAY_TO é obrigatório em PAYMENT_MODE=x402-mainnet.")
        if not is_valid_address(self.pay_to):
            raise ConfigError("PAY_TO não é um endereço Algorand válido.")
        if self.x402_network not in (*ALGORAND_MAINNET_ALIASES, ALGORAND_MAINNET_CAIP2):
            raise ConfigError("X402_NETWORK deve indicar Algorand Mainnet em x402-mainnet.")
        url = urlparse(self.x402_facilitator_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ConfigError("X402_FACILITATOR_URL deve ser uma URL https válida sem credenciais.")
        self.price_atomic

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

    @property
    def x402_caip2(self) -> str:
        from x402.mechanisms.avm import (
            ALGORAND_MAINNET_CAIP2,
            ALGORAND_TESTNET_CAIP2,
        )

        if self.payment_mode == "x402-mainnet":
            return ALGORAND_MAINNET_CAIP2
        if self.payment_mode == "x402-testnet":
            return ALGORAND_TESTNET_CAIP2
        raise ConfigError("Perfil x402 não está ativo.")

    @property
    def x402_usdc_asa_id(self) -> int:
        from x402.mechanisms.avm import (
            USDC_MAINNET_ASA_ID,
            USDC_TESTNET_ASA_ID,
        )

        if self.payment_mode == "x402-mainnet":
            return USDC_MAINNET_ASA_ID
        if self.payment_mode == "x402-testnet":
            return USDC_TESTNET_ASA_ID
        raise ConfigError("Perfil x402 não está ativo.")


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
    raw_mainnet = os.getenv("ENABLE_MAINNET_X402", "false").strip().lower()
    if raw_mainnet not in ("true", "false"):
        raise ConfigError("ENABLE_MAINNET_X402 deve ser true ou false.")

    def _int_or_none(name: str) -> int | None:
        raw = os.getenv(name, "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} deve ser um inteiro.") from exc

    try:
        preview_ttl = float(
            os.getenv("PREVIEW_CACHE_TTL_SECONDS", str(DEFAULT_PREVIEW_CACHE_TTL_SECONDS)).strip()
        )
    except ValueError as exc:
        raise ConfigError("PREVIEW_CACHE_TTL_SECONDS deve ser numérico.") from exc
    try:
        preview_rate = int(
            os.getenv(
                "PREVIEW_RATE_LIMIT_PER_MINUTE", str(DEFAULT_PREVIEW_RATE_LIMIT_PER_MINUTE)
            ).strip()
        )
    except ValueError as exc:
        raise ConfigError("PREVIEW_RATE_LIMIT_PER_MINUTE deve ser um inteiro.") from exc
    raw_credit_cost = os.getenv("HELIUS_CREDIT_COST_USD", "").strip()
    try:
        credit_cost = Decimal(raw_credit_cost) if raw_credit_cost else DEFAULT_HELIUS_CREDIT_COST_USD
    except InvalidOperation as exc:
        raise ConfigError("HELIUS_CREDIT_COST_USD deve ser numérico.") from exc

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
        enable_mainnet_x402=raw_mainnet == "true",
        preview_cache_ttl_seconds=preview_ttl,
        preview_rate_limit_per_minute=preview_rate,
        helius_daily_budget_credits=_int_or_none("HELIUS_DAILY_BUDGET_CREDITS"),
        helius_per_query_credit_limit=_int_or_none("HELIUS_PER_QUERY_CREDIT_LIMIT"),
        helius_credit_cost_usd=credit_cost,
        telemetry_db_path=(
            os.getenv("TELEMETRY_DB_PATH", DEFAULT_TELEMETRY_DB_PATH).strip()
            or DEFAULT_TELEMETRY_DB_PATH
        ),
        quota_db_path=(
            os.getenv("QUOTA_DB_PATH", DEFAULT_TELEMETRY_DB_PATH).strip() or DEFAULT_TELEMETRY_DB_PATH
        ),
        trusted_proxy_header=(os.getenv("TRUSTED_PROXY_HEADER", "").strip() or None),
        trusted_proxy_cidrs=tuple(
            c.strip() for c in os.getenv("TRUSTED_PROXY_CIDRS", "").split(",") if c.strip()
        ),
        quota_ip_pseudonymization_salt=(os.getenv("QUOTA_IP_PSEUDONYMIZATION_SALT", "").strip() or None),
        telemetry_retention_days=_int_or_none("TELEMETRY_RETENTION_DAYS") or 90,
        retention_purge_interval_seconds=_int_or_none("RETENTION_PURGE_INTERVAL_SECONDS") or 3600,
        quota_anonymous_per_minute=_int_or_none("QUOTA_ANONYMOUS_PER_MINUTE") or 10,
        quota_anonymous_per_day=_int_or_none("QUOTA_ANONYMOUS_PER_DAY") or 50,
        quota_anonymous_distinct_mints_per_day=_int_or_none("QUOTA_ANONYMOUS_DISTINCT_MINTS_PER_DAY") or 15,
        quota_registered_per_minute=_int_or_none("QUOTA_REGISTERED_PER_MINUTE") or 30,
        quota_registered_per_day=_int_or_none("QUOTA_REGISTERED_PER_DAY") or 300,
        quota_registered_distinct_mints_per_day=_int_or_none("QUOTA_REGISTERED_DISTINCT_MINTS_PER_DAY") or 60,
    )
