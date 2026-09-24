from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models import DataQuality, Intelligence


@dataclass
class ProviderResult:
    status: str
    intelligence: Intelligence
    data_quality: DataQuality = field(default_factory=lambda: DataQuality(status="UNKNOWN"))


class IntelligenceProvider(ABC):
    """Contrato interno entre a API e a fonte de inteligência.

    Uma futura AlphaHunterProvider implementa esta interface sem alterar
    o contrato público de /v1/token/investigate.
    """

    name: str = "abstract"

    @abstractmethod
    def investigate(self, chain: str, mint: str) -> ProviderResult: ...
