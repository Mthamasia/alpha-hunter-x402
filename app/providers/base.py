from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models import DataQuality, Intelligence


@dataclass
class ProviderResult:
    status: str
    intelligence: Intelligence
    data_quality: DataQuality = field(default_factory=lambda: DataQuality(status="UNKNOWN"))
    # Freshness, surfaced separately so a free/limited caller (e.g. the
    # preview endpoint) can see data age without receiving the full
    # premium intelligence payload. None when the provider has no
    # freshness concept (e.g. stub, NO_DATA).
    age_seconds: int | None = None
    is_stale: bool | None = None


class IntelligenceProvider(ABC):
    """Contrato interno entre a API e a fonte de inteligência.

    Uma futura AlphaHunterProvider implementa esta interface sem alterar
    o contrato público de /v1/token/investigate.
    """

    name: str = "abstract"

    @abstractmethod
    def investigate(self, chain: str, mint: str) -> ProviderResult: ...
