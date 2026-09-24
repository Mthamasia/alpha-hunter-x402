from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.models import DataQuality, Intelligence
from app.providers.base import IntelligenceProvider, ProviderResult

BRIDGE_SCHEMA_VERSION = "1.0"


class ProviderError(RuntimeError):
    status_code = 502
    code = "provider_error"
    message = "intelligence provider returned an invalid response"


class ProviderUnavailableError(ProviderError):
    status_code = 503
    code = "provider_unavailable"
    message = "intelligence provider unavailable"


class ProviderConfigurationError(ProviderError):
    status_code = 500
    code = "provider_configuration_error"
    message = "intelligence provider authentication failed"


class AlphaHunterProvider(IntelligenceProvider):
    """Read-only HTTP client for the Alpha Hunter X bridge."""

    name = "alpha_hunter"

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.ahx_bridge_url.rstrip("/")
        self._service_token = settings.ahx_bridge_service_token
        self._timeout = settings.ahx_bridge_timeout_seconds

    def investigate(self, chain: str, mint: str) -> ProviderResult:
        url = f"{self._base_url}/v1/x402/token/{quote(mint, safe='')}"

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(
                    url,
                    headers={
                        "X-AHX-Bridge-Token": self._service_token,
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError() from exc
        except httpx.NetworkError as exc:
            raise ProviderUnavailableError() from exc

        if response.status_code == 404:
            return ProviderResult(
                status="NO_DATA",
                intelligence=Intelligence(),
                data_quality=DataQuality(
                    status="UNAVAILABLE",
                    limitations=[
                        "Alpha Hunter X bridge has no data for this token."
                    ],
                ),
            )

        if response.status_code in (401, 403):
            raise ProviderConfigurationError()

        if response.status_code >= 500:
            raise ProviderUnavailableError()

        if response.status_code != 200:
            raise ProviderError()

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError() from exc

        return self._map_response(payload, mint)

    @staticmethod
    def _map_response(
        payload: Any,
        requested_mint: str,
    ) -> ProviderResult:
        if not isinstance(payload, dict):
            raise ProviderError()

        if payload.get("schema_version") != BRIDGE_SCHEMA_VERSION:
            raise ProviderError()

        if payload.get("mint") != requested_mint:
            raise ProviderError()

        # The bridge exposes freshness metadata separately from
        # the public data-quality classification.
        freshness = payload.get("freshness")
        data_quality = payload.get("data_quality")

        if not isinstance(freshness, dict):
            raise ProviderError()

        if not isinstance(data_quality, dict):
            raise ProviderError()

        freshness_status = data_quality.get("status")
        limitations = data_quality.get("limitations", [])

        if not isinstance(freshness_status, str) or not freshness_status:
            raise ProviderError()

        if not isinstance(limitations, list) or not all(
            isinstance(item, str) for item in limitations
        ):
            raise ProviderError()

        # These fields are structured objects in the real AHX bridge
        # contract. Evidence remains a list.
        creator = payload.get("creator")
        wallet_activity = payload.get("wallet_activity")
        behavioral_findings = payload.get("behavioral_findings")
        concentration = payload.get("concentration")
        evidence = payload.get("evidence", [])

        if creator is not None and not isinstance(creator, dict):
            raise ProviderError()

        if wallet_activity is not None and not isinstance(
            wallet_activity, dict
        ):
            raise ProviderError()

        if behavioral_findings is not None and not isinstance(
            behavioral_findings, dict
        ):
            raise ProviderError()

        if not isinstance(evidence, list):
            raise ProviderError()

        bridge_status = payload.get("status", freshness_status)

        if not isinstance(bridge_status, str) or not bridge_status:
            bridge_status = freshness_status

        public_status = (
            "STALE"
            if freshness_status == "STALE"
            else bridge_status
        )

        return ProviderResult(
            status=public_status,
            intelligence=Intelligence(
                creator=creator,
                wallet_activity=wallet_activity,
                concentration=concentration,
                behavioral_findings=behavioral_findings,
                evidence=evidence,
            ),
            data_quality=DataQuality(
                status=freshness_status,
                limitations=limitations,
            ),
        )