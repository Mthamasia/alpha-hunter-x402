from __future__ import annotations

import asyncio
import ipaddress
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import SCHEMA_VERSION, __version__
from app.config import Settings, load_settings
from app.models import InvestigateRequest, InvestigateResponse, PreviewResponse
from app.payments.gate import (
    PAYMENT_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PaymentGate,
    PaymentRequired,
)
from app.providers import (
    IntelligenceProvider,
    ProviderError,
    create_intelligence_provider,
)
from app.quota import (
    TIER_ANONYMOUS,
    TIER_FREE_KEY,
    QuotaStore,
    TierLimits,
    hash_identifier,
    hash_key,
)
from app.ratelimit import TTLCache
from app.telemetry import (
    REVENUE_FAILED,
    REVENUE_NONE,
    REVENUE_PENDING,
    REVENUE_SETTLED,
    REVENUE_SIMULATED,
    ROUTE_INVESTIGATE,
    ROUTE_PREVIEW,
    SERVED_FROM_BLOCKED,
    SERVED_FROM_BRIDGE,
    SERVED_FROM_CACHE,
    SERVED_FROM_STUB,
    CostLedger,
    HeliusCallTracker,
    new_request_id,
    record_query,
)

MAX_BODY_BYTES = 4096
AGENT_ID_HEADER = "X-Agent-Id"  # unauthenticated client claim; never gates quota or identity
API_KEY_HEADER = "X-API-Key"
MAX_AGENT_ID_LEN = 128
# The official x402-avm library's settlement response header -- distinct
# from app.payments.gate.PAYMENT_RESPONSE_HEADER ("X-PAYMENT-RESPONSE"),
# which only exists for this project's own simulated `test` mode.
X402_SETTLEMENT_HEADER = "PAYMENT-RESPONSE"

logger = logging.getLogger("ahx")


def _error(status: int, code: str, message: str, request_id: str | None, **extra) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id, **extra}}
    return JSONResponse(status_code=status, content=body)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _agent_id(request: Request) -> str | None:
    """An UNAUTHENTICATED client claim (`X-Agent-Id`). Recorded in
    telemetry as a label only; never used to resolve identity or gate
    quota -- see `_resolve_subject_and_tier`."""

    raw = request.headers.get(AGENT_ID_HEADER)
    if not raw:
        return None
    raw = raw.strip()
    if not raw or len(raw) > MAX_AGENT_ID_LEN:
        return None
    return raw


def _peer_is_trusted_proxy(request: Request, settings: Settings) -> bool:
    """A configured header NAME alone does not stop a client from simply
    sending that header itself. The header is only honored when the
    IMMEDIATE TCP peer's address is itself inside an operator-confirmed
    CIDR allowlist (TRUSTED_PROXY_CIDRS) -- the standard "trusted hop"
    model. Config validation already refuses TRUSTED_PROXY_HEADER without
    TRUSTED_PROXY_CIDRS, so an empty list here just means nothing is
    trusted yet, not a silent bypass."""

    if not settings.trusted_proxy_cidrs or not request.client:
        return False
    try:
        peer = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    for cidr in settings.trusted_proxy_cidrs:
        if peer in ipaddress.ip_network(cidr, strict=False):
            return True
    return False


def _resolve_client_ip(request: Request, settings: Settings) -> str:
    if settings.trusted_proxy_header and _peer_is_trusted_proxy(request, settings):
        value = request.headers.get(settings.trusted_proxy_header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _resolve_subject_and_tier(
    request: Request, settings: Settings, quota_store: QuotaStore
) -> tuple[str, str]:
    """Returns (subject_id, tier) for quota purposes. An API key always
    wins over IP if present and valid; an unknown/revoked key falls back
    to the anonymous IP-based subject rather than erroring, so a bad key
    degrades to the free tier instead of blocking the request outright.

    The IP itself is never stored raw: it is pseudonymized (HMAC if
    QUOTA_IP_PSEUDONYMIZATION_SALT is configured, else plain SHA-256) before it
    becomes a subject_id, consistent with treating it as the personal
    data it can be."""

    raw_key = request.headers.get(API_KEY_HEADER)
    if raw_key:
        tier = quota_store.resolve_key_tier(raw_key)
        if tier is not None:
            return f"key:{hash_key(raw_key)}", tier
    ip = _resolve_client_ip(request, settings)
    return f"ip:{hash_identifier(ip, settings.quota_ip_pseudonymization_salt)}", TIER_ANONYMOUS


# Only independently traceable on-chain references qualify as paid
# evidence; a source label such as {"source": "bridge"} is not a reference.
_TRACE_KEYS = (
    "transaction_signature", "tx_signature", "signature",
    "transaction_id", "tx_hash", "tx_id", "event_id", "reference", "url",
)


def _traceable_evidence(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    for key in _TRACE_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip() and value.strip().upper() != "UNKNOWN":
            return True
    # A recorded Solana slot is independently locatable, even without a
    # transaction ID, but bool and zero are not valid observed slots.
    slot = item.get("slot")
    return isinstance(slot, int) and not isinstance(slot, bool) and slot > 0


def _substantive_finding(intelligence: Any) -> bool:
    # A known creator address alone or a guessed trust score is not a
    # behavioral finding. Demand observed outcomes rather than a profile stub.
    creator = getattr(intelligence, "creator", None)
    if isinstance(creator, dict):
        profile = creator.get("profile")
        if isinstance(profile, dict):
            for field in ("verified_launches", "confirmed_outcomes", "observed_launches"):
                n = profile.get(field)
                if type(n) is int and n > 0:
                    return True

    wallet = getattr(intelligence, "wallet_activity", None)
    if isinstance(wallet, dict):
        profile = wallet.get("profile")
        if isinstance(profile, dict):
            count = profile.get("total_events")
            if type(count) is int and count > 0:
                return True
        events = wallet.get("recent_events")
        if isinstance(events, list) and any(isinstance(item, dict) and item for item in events):
            return True

    findings = getattr(intelligence, "behavioral_findings", None)
    if isinstance(findings, dict):
        alerts = findings.get("alerts")
        if isinstance(alerts, list) and any(isinstance(item, dict) and item for item in alerts):
            return True
        explanation = findings.get("explanation")
        if isinstance(explanation, str) and len(explanation.strip()) >= 30:
            return True
    return False


def _billable_intelligence(result: Any) -> bool:
    """Mainnet settlement requires fresh, traceable, substantive evidence.

    An empty dict, source-only label, creator address, or unexplained score
    must NEVER be enough to accept payment. This is a sale eligibility guard,
    not a trigger for fetching more data or consuming Helius credits.
    """
    quality = getattr(getattr(result, "data_quality", None), "status", None)
    if quality != "FRESH":
        return False

    intelligence = getattr(result, "intelligence", None)
    if intelligence is None:
        return False
    evidence = getattr(intelligence, "evidence", None)
    if not isinstance(evidence, list) or not any(_traceable_evidence(i) for i in evidence):
        return False
    return _substantive_finding(intelligence)


def create_app(
    settings: Settings | None = None,
    provider: IntelligenceProvider | None = None,
    facilitator_client: Any = None,
) -> FastAPI:
    settings = settings or load_settings()
    provider = provider or create_intelligence_provider(settings)
    gate = PaymentGate(settings)
    preview_cache = TTLCache(ttl_seconds=settings.preview_cache_ttl_seconds)
    cost_ledger = CostLedger(settings.telemetry_db_path)
    quota_store = QuotaStore(
        settings.quota_db_path,
        tier_limits={
            TIER_ANONYMOUS: TierLimits(
                per_minute=settings.quota_anonymous_per_minute,
                per_day=settings.quota_anonymous_per_day,
                distinct_mints_per_day=settings.quota_anonymous_distinct_mints_per_day,
            ),
            TIER_FREE_KEY: TierLimits(
                per_minute=settings.quota_registered_per_minute,
                per_day=settings.quota_registered_per_day,
                distinct_mints_per_day=settings.quota_registered_distinct_mints_per_day,
            ),
        },
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from app.retention import periodic_purge_loop

        task = asyncio.create_task(
            periodic_purge_loop(
                quota_store,
                cost_ledger,
                interval_seconds=settings.retention_purge_interval_seconds,
                telemetry_retention_days=settings.telemetry_retention_days,
            )
        )
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="AHX x402 API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.provider = provider
    app.state.gate = gate
    app.state.preview_cache = preview_cache
    app.state.cost_ledger = cost_ledger
    app.state.quota_store = quota_store
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "PAYMENT-SIGNATURE"],
            expose_headers=["PAYMENT-REQUIRED", "PAYMENT-RESPONSE", "X-Request-ID"],
        )

    if settings.payment_mode in ("x402-testnet", "x402-mainnet"):
        from x402.http import decode_payment_response_header

        from app.payments.x402_avm import build_x402_middleware

        x402_mw = build_x402_middleware(settings, facilitator_client)

        # Registrada antes de request_context, então roda DEPOIS dela (Starlette:
        # a última middleware registrada é a mais externa).
        @app.middleware("http")
        async def x402_payment(request: Request, call_next):
            try:
                response = await x402_mw(request, call_next)
            except Exception:
                # Fail closed: facilitator indisponível / rota não suportada -> 503.
                logger.exception("x402 payment layer error rid=%s", _rid(request))
                return _error(503, "payment_unavailable",
                              "payment facilitator unavailable", _rid(request))

            # Settlement happens INSIDE x402_mw, after the route already ran
            # (and already wrote a PENDING telemetry row -- see `investigate`
            # below). Only now do we know the real outcome, so only now do
            # we finalize revenue to SETTLED/FAILED. If this header is
            # absent, settlement was never attempted (e.g. no payment, or
            # verification failed before the route ran) and there is no
            # pending row to finalize either -- `finalize_revenue` is a
            # harmless no-op in that case.
            #
            # CRITICAL: a telemetry failure here must NEVER change what the
            # client receives. A customer who paid and whose settlement
            # already succeeded must get their 200 response even if SQLite
            # itself is down right now -- that is a reconciliation problem,
            # not a reason to turn a successful paid delivery into a 503.
            # So this entire block is isolated: any exception is logged at
            # CRITICAL (a real paid settlement whose revenue bookkeeping is
            # now unreconciled, needing manual follow-up) and swallowed.
            settlement_header = response.headers.get(X402_SETTLEMENT_HEADER)
            request_id = _rid(request)
            if request_id:
                try:
                    if settlement_header:
                        settlement = decode_payment_response_header(settlement_header)
                        if settlement.success:
                            cost_ledger.finalize_revenue(
                                request_id, revenue_kind=REVENUE_SETTLED, revenue_usd=settings.price_usd
                            )
                        else:
                            cost_ledger.finalize_revenue(
                                request_id, revenue_kind=REVENUE_FAILED, revenue_usd=None
                            )
                    elif response.status_code != 200:
                        # The library swaps a failed-settlement response for a
                        # 402 with no PAYMENT-RESPONSE header at all (confirmed
                        # empirically, not documented). `finalize_revenue` only
                        # touches a row that is still PENDING, so this is a
                        # harmless no-op for every other non-200 response that
                        # never recorded a pending row in the first place (no
                        # payment provided, validation errors, unreachable
                        # facilitator, mainnet no-charge decisions, ...).
                        cost_ledger.finalize_revenue(
                            request_id, revenue_kind=REVENUE_FAILED, revenue_usd=None
                        )
                except Exception:
                    logger.critical(
                        "UNRECONCILED SETTLEMENT: telemetry finalization failed after a real "
                        "x402 settlement outcome was already known (rid=%s, settled=%s). "
                        "The client response was NOT altered. Manual reconciliation needed.",
                        request_id,
                        response.status_code == 200,
                        exc_info=True,
                    )
            return response

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        # request_id sempre gerado no servidor (não confiamos em IDs do cliente).
        request.state.request_id = str(uuid.uuid4())
        cl = request.headers.get("content-length")
        if cl is not None:
            if not cl.isdigit():
                return _error(400, "bad_request", "invalid Content-Length", _rid(request))
            if int(cl) > MAX_BODY_BYTES:
                return _error(413, "payload_too_large", "request body too large", _rid(request))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Cache-Control"] = "no-store"
        # Loga apenas metadados; nunca headers/body (podem conter provas de pagamento).
        logger.info("%s %s -> %s rid=%s", request.method, request.url.path,
                    response.status_code, request.state.request_id)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Não ecoa o input do cliente; apenas localização e mensagem.
        details = [
            {"loc": [str(p) for p in err.get("loc", ())], "msg": str(err.get("msg", ""))[:200]}
            for err in exc.errors()
        ]
        return _error(422, "validation_error", "invalid request", _rid(request), details=details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail), _rid(request))

    @app.exception_handler(PaymentRequired)
    async def _payment_required(request: Request, exc: PaymentRequired):
        return JSONResponse(status_code=402, content={**exc.body, "request_id": _rid(request)})

    @app.exception_handler(ProviderError)
    async def _provider_error(request: Request, exc: ProviderError):
        logger.error("%s rid=%s", exc.code, _rid(request))
        return _error(exc.status_code, exc.code, exc.message, _rid(request))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error rid=%s", _rid(request))
        return _error(500, "internal_error", "internal server error", _rid(request))

    @app.get("/", include_in_schema=False)
    def service_index():
        """Human/machine entrypoint; intentionally does not make paid requests."""
        return {
            "name": "Alpha Hunter X",
            "description": "Behavioral on-chain intelligence API for Solana agents",
            "website": "https://alpha-hunter-x-intelligence.vercel.app/",
            "openapi": "/openapi.json",
            "x402_discovery": "/.well-known/x402",
            "preview": "/v1/token/preview",
            "investigate": "/v1/token/investigate",
        }

    @app.get("/.well-known/x402")
    def x402_discovery():
        """AHX-specific public manifest, not a standardized Bazaar listing."""
        return {
            "format": "ahx-discovery-v1",
            "name": "Alpha Hunter X",
            "description": "Solana token creator and wallet behavioral intelligence with evidence and data-quality indicators",
            "website": "https://alpha-hunter-x-intelligence.vercel.app/",
            "api_documentation": "/openapi.json",
            "resource": {
                "method": "POST",
                "path": "/v1/token/investigate",
                "content_type": "application/json",
                "example_request": {
                    "chain": "solana",
                    "mint": "So11111111111111111111111111111111111111112",
                },
            },
            "resources": [
                {
                    "method": "POST",
                    "path": "/v1/token/preview",
                    "name": "Free token availability/freshness preview",
                    "description": (
                        "Free, no payment required. Returns only availability, "
                        "data age, FRESH/STALE classification and data-quality "
                        "limitations for a Solana token -- never creator, wallet "
                        "activity, behavioral findings or evidence. Rate-limited "
                        "per caller; see /v1/token/investigate for the full "
                        "paid investigation."
                    ),
                    "price_usd": "0",
                    "payment": {"required": False},
                    "rate_limits": {
                        "anonymous": {
                            "per_minute": settings.quota_anonymous_per_minute,
                            "per_day": settings.quota_anonymous_per_day,
                            "distinct_mints_per_day": settings.quota_anonymous_distinct_mints_per_day,
                        },
                        "api_key": {
                            "per_minute": settings.quota_registered_per_minute,
                            "per_day": settings.quota_registered_per_day,
                            "distinct_mints_per_day": settings.quota_registered_distinct_mints_per_day,
                            "header": API_KEY_HEADER,
                        },
                    },
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "chain": {"type": "string", "const": "solana"},
                            "mint": {
                                "type": "string",
                                "minLength": 32,
                                "maxLength": 44,
                            },
                        },
                        "required": ["chain", "mint"],
                        "additionalProperties": False,
                    },
                },
                {
                    "method": "POST",
                    "path": "/v1/token/investigate",
                    "name": "Solana creator and wallet behavioral intelligence",
                    "description": (
                        "Investigate token creator behavior, wallet activity, "
                        "concentration, historical behavioral findings and "
                        "traceable evidence for AI trading agents. "
                        "Returns explicit data-quality and freshness status. "
                        "Does not execute trades."
                    ),
                    "price_usd": str(settings.price_usd),
                    "payment": {"required": True, "protocol": "x402"},
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "chain": {"type": "string", "const": "solana"},
                            "mint": {
                                "type": "string",
                                "minLength": 32,
                                "maxLength": 44,
                            },
                        },
                        "required": ["chain", "mint"],
                        "additionalProperties": False,
                    },
                },
            ],
            "payment": {
                "protocol": "x402",
                "mode": settings.payment_mode,
                "negotiation": "Make an unsigned POST to receive the PAYMENT-REQUIRED offer when x402 is enabled.",
            },
            "data_quality": "Check status and data_quality in every paid response.",
        }

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        return {"status": "ready", "provider": provider.name}

    @app.get("/version")
    def version():
        return {
            "name": "ahx-x402-api",
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "app_env": settings.app_env,
            "payment_mode": settings.payment_mode,
            "provider": provider.name,
        }

    def _revenue_for_investigate(billed: bool) -> tuple[Decimal | None, str]:
        if settings.payment_mode == "disabled":
            return None, REVENUE_NONE
        if settings.payment_mode == "test":
            # Simulated mode has no real async facilitator step -- the
            # "settlement" already happened synchronously in `gate.verify`.
            return settings.price_usd, REVENUE_SIMULATED
        # x402-testnet / x402-mainnet: verification already happened (in the
        # x402 middleware, before this handler ran), but REAL settlement
        # happens in that same middleware AFTER this handler returns -- so
        # revenue can only be PENDING here. The middleware finalizes this
        # exact row to SETTLED/FAILED once the facilitator actually confirms
        # (see `x402_payment` above). Recording SETTLED here, before that
        # confirmation exists, is exactly the bug this stage fixes: a
        # failed settlement must never look like revenue.
        if billed:
            return None, REVENUE_PENDING
        return None, REVENUE_NONE

    @app.post("/v1/token/investigate", response_model=InvestigateResponse)
    def investigate(body: InvestigateRequest, request: Request):
        decision = gate.verify(request.headers.get(PAYMENT_HEADER), str(request.url.path))
        tracker = HeliusCallTracker()
        result = provider.investigate(body.chain, body.mint)
        billed = settings.payment_mode != "x402-mainnet" or _billable_intelligence(result)
        if settings.payment_mode == "x402-mainnet" and not billed:
            # x402 settles only successful endpoint responses: do not charge
            # for missing, old, or unsubstantiated intelligence.
            revenue_usd, revenue_kind = None, REVENUE_NONE
            record_query(
                cost_ledger,
                tracker=tracker,
                request_id=request.state.request_id,
                route=ROUTE_INVESTIGATE,
                chain=body.chain,
                mint=body.mint,
                agent_id=_agent_id(request),
                served_from=SERVED_FROM_BRIDGE if provider.name == "alpha_hunter" else SERVED_FROM_STUB,
                revenue_usd=revenue_usd,
                revenue_kind=revenue_kind,
                credit_cost_usd=settings.helius_credit_cost_usd,
            )
            return _error(
                503,
                "intelligence_not_ready",
                "Current evidence is not available for this token; no payment settled.",
                _rid(request),
            )
        revenue_usd, revenue_kind = _revenue_for_investigate(billed)
        record_query(
            cost_ledger,
            tracker=tracker,
            request_id=request.state.request_id,
            route=ROUTE_INVESTIGATE,
            chain=body.chain,
            mint=body.mint,
            agent_id=_agent_id(request),
            served_from=SERVED_FROM_BRIDGE if provider.name == "alpha_hunter" else SERVED_FROM_STUB,
            revenue_usd=revenue_usd,
            revenue_kind=revenue_kind,
            credit_cost_usd=settings.helius_credit_cost_usd,
        )
        payload = InvestigateResponse(
            request_id=request.state.request_id,
            chain=body.chain,
            mint=body.mint,
            status=result.status,
            generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            intelligence=result.intelligence,
            data_quality=result.data_quality,
        )
        response = JSONResponse(content=payload.model_dump(mode="json"))
        if decision.response_header:
            response.headers[PAYMENT_RESPONSE_HEADER] = decision.response_header
        return response

    @app.post("/v1/token/preview", response_model=PreviewResponse)
    def preview(body: InvestigateRequest, request: Request):
        """Free, quota-limited, cached summary. Calls the exact same
        provider/bridge path as the paid endpoint (so this is a real
        connectivity check, not a canned response) but returns only
        availability/freshness/data-quality -- never the premium
        `intelligence` payload.

        Quota is checked BEFORE touching the cache or the bridge, and is
        persistent/atomic (app/quota.py) -- not the simple in-process
        limiter this endpoint shipped with initially. Database/cache-only
        is an absolute invariant: nothing below this point ever has a
        code path to Helius, successful or not (see tests/test_preview.py
        ::test_preview_never_calls_helius_on_any_path for the explicit,
        always-on proof).
        """

        subject_id, tier = _resolve_subject_and_tier(request, settings, quota_store)
        decision = quota_store.check_and_consume(subject_id, tier, body.mint)
        if not decision.allowed:
            record_query(
                cost_ledger,
                tracker=HeliusCallTracker(),
                request_id=request.state.request_id,
                route=ROUTE_PREVIEW,
                chain=body.chain,
                mint=body.mint,
                agent_id=_agent_id(request),
                served_from=SERVED_FROM_BLOCKED,
                revenue_usd=None,
                revenue_kind=REVENUE_NONE,
                credit_cost_usd=settings.helius_credit_cost_usd,
            )
            retry_after = decision.retry_after_seconds or 60
            response = _error(
                429,
                "rate_limited",
                "preview quota exceeded; try again later",
                _rid(request),
                reason=decision.reason,
            )
            response.headers["Retry-After"] = str(retry_after)
            return response

        cache_key = (body.chain, body.mint)
        cached = preview_cache.get(cache_key)
        tracker = HeliusCallTracker()
        if cached is not None:
            result = cached
            served_from = SERVED_FROM_CACHE
        else:
            result = provider.investigate(body.chain, body.mint)
            preview_cache.set(cache_key, result)
            served_from = SERVED_FROM_BRIDGE if provider.name == "alpha_hunter" else SERVED_FROM_STUB

        record_query(
            cost_ledger,
            tracker=tracker,
            request_id=request.state.request_id,
            route=ROUTE_PREVIEW,
            chain=body.chain,
            mint=body.mint,
            agent_id=_agent_id(request),
            served_from=served_from,
            revenue_usd=None,
            revenue_kind=REVENUE_NONE,
            credit_cost_usd=settings.helius_credit_cost_usd,
        )

        available = result.status not in ("NO_DATA", "NO_DATA_SOURCE")
        payload = PreviewResponse(
            request_id=request.state.request_id,
            chain=body.chain,
            mint=body.mint,
            generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            available=available,
            status=result.status,
            age_seconds=result.age_seconds,
            is_stale=result.is_stale,
            data_quality=result.data_quality,
        )
        return JSONResponse(content=payload.model_dump(mode="json"))

    return app


app = create_app()
