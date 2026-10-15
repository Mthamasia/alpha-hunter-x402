from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import SCHEMA_VERSION, __version__
from app.config import Settings, load_settings
from app.models import InvestigateRequest, InvestigateResponse
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

MAX_BODY_BYTES = 4096
PREMIUM_PATH = "/v1/token/investigate"

logger = logging.getLogger("ahx")


def _error(status: int, code: str, message: str, request_id: str | None, **extra) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id, **extra}}
    return JSONResponse(status_code=status, content=body)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _billable_intelligence(result: Any) -> bool:
    """A Mainnet sale requires observed, current, substantive evidence.

    This is a sale-eligibility check, not a live-data collection trigger.
    Historical or empty reports remain available in internal modes.
    """
    quality = getattr(getattr(result, "data_quality", None), "status", None)
    if quality != "FRESH":
        return False

    intelligence = getattr(result, "intelligence", None)
    if intelligence is None or not getattr(intelligence, "evidence", None):
        return False

    # One evidence item plus at least one meaningful intelligence finding.
    for attr in ("creator", "wallet_activity", "concentration", "behavioral_findings"):
        if getattr(intelligence, attr, None):
            return True
    return False


def create_app(
    settings: Settings | None = None,
    provider: IntelligenceProvider | None = None,
    facilitator_client: Any = None,
) -> FastAPI:
    settings = settings or load_settings()
    provider = provider or create_intelligence_provider(settings)
    gate = PaymentGate(settings)

    app = FastAPI(title="AHX x402 API", version=__version__)
    app.state.settings = settings
    app.state.provider = provider
    app.state.gate = gate
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
        from app.payments.x402_avm import build_x402_middleware

        x402_mw = build_x402_middleware(settings, facilitator_client)

        # Registrada antes de request_context, então roda DEPOIS dela (Starlette:
        # a última middleware registrada é a mais externa).
        @app.middleware("http")
        async def x402_payment(request: Request, call_next):
            try:
                return await x402_mw(request, call_next)
            except Exception:
                # Fail closed: facilitator indisponível / rota não suportada -> 503.
                logger.exception("x402 payment layer error rid=%s", _rid(request))
                return _error(503, "payment_unavailable",
                              "payment facilitator unavailable", _rid(request))

    # Registered after x402_payment and before request_context: Starlette
    # runs it OUTSIDE the x402 layer (so nothing is verified, offered or
    # settled) and INSIDE request_context (so the response has an id).
    @app.middleware("http")
    async def premium_maintenance(request: Request, call_next):
        if not settings.premium_sales_enabled and request.url.path == PREMIUM_PATH:
            response = _error(
                503,
                "premium_maintenance",
                "Premium investigation is temporarily unavailable. No payment was "
                "requested, verified or settled, and no intelligence was produced.",
                _rid(request),
            )
            response.headers["Retry-After"] = "3600"
            return response
        return await call_next(request)

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
                    "sales_status": "available" if settings.premium_sales_enabled else "maintenance",
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
                }
            ],
            "payment": {
                "protocol": "x402",
                "mode": settings.payment_mode,
                "negotiation": "Make an unsigned POST to receive the PAYMENT-REQUIRED offer when x402 is enabled.",
            },
            "data_quality": "Check status and data_quality in every paid response.",
            "sales_status": "available" if settings.premium_sales_enabled else "maintenance",
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
            "premium_sales": "available" if settings.premium_sales_enabled else "maintenance",
        }

    @app.post("/v1/token/investigate", response_model=InvestigateResponse)
    def investigate(body: InvestigateRequest, request: Request):
        decision = gate.verify(request.headers.get(PAYMENT_HEADER), str(request.url.path))
        result = provider.investigate(body.chain, body.mint)
        if settings.payment_mode == "x402-mainnet" and not _billable_intelligence(result):
            # x402 settles only successful endpoint responses: do not charge
            # for missing, old, or unsubstantiated intelligence.
            return _error(
                503,
                "intelligence_not_ready",
                "Current evidence is not available for this token; no payment settled.",
                _rid(request),
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

    return app


app = create_app()
