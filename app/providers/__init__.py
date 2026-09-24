from app.config import Settings
from app.providers.alpha_hunter import (
    AlphaHunterProvider,
    ProviderConfigurationError,
    ProviderError,
    ProviderUnavailableError,
)
from app.providers.base import IntelligenceProvider, ProviderResult
from app.providers.stub import StubIntelligenceProvider


def create_intelligence_provider(settings: Settings) -> IntelligenceProvider:
    if settings.intelligence_provider == "alpha_hunter":
        return AlphaHunterProvider(settings)
    return StubIntelligenceProvider()


__all__ = [
    "AlphaHunterProvider",
    "IntelligenceProvider",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderResult",
    "ProviderUnavailableError",
    "StubIntelligenceProvider",
    "create_intelligence_provider",
]
