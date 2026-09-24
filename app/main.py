from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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

logger = logging.getLogger("ahx")


def _error(status: int, code: str, message: str, request_id: str | None, **extra) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id, **extra}}
    return JSONResponse(status_code=status, content=body)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


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

    if settings.payment_mode == "x402-testnet":
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

    @app.get("/health")
    def health():
        return {"status": "ok"}

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

    @app.post("/v1/token/investigate", response_model=InvestigateResponse)
    def investigate(body: InvestigateRequest, request: Request):
        decision = gate.verify(request.headers.get(PAYMENT_HEADER), str(request.url.path))
        result = provider.investigate(body.chain, body.mint)
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
