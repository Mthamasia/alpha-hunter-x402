from __future__ import annotations

from app.models import DataQuality, Intelligence
from app.providers.base import IntelligenceProvider, ProviderResult

NO_DATA_SOURCE = "NO_DATA_SOURCE"


class StubIntelligenceProvider(IntelligenceProvider):
    """Provider sem fonte de dados. Nunca fabrica inteligência."""

    name = "stub"

    def investigate(self, chain: str, mint: str) -> ProviderResult:
        return ProviderResult(
            status=NO_DATA_SOURCE,
            intelligence=Intelligence(),
            data_quality=DataQuality(
                status="UNAVAILABLE",
                limitations=[
                    "Nenhuma fonte de inteligência está conectada (provider=stub).",
                    "O Alpha Hunter X ainda não foi integrado; nenhum dado on-chain foi consultado.",
                    "Todos os campos de 'intelligence' estão vazios intencionalmente; "
                    "nenhum dado foi inferido ou fabricado.",
                ],
            ),
        )
