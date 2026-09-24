from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import SCHEMA_VERSION

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58decode(value: str) -> bytes:
    """Decodifica base58 (alfabeto Bitcoin/Solana). Levanta ValueError se inválido."""
    num = 0

    for ch in value:
        idx = _B58_INDEX.get(ch)

        if idx is None:
            raise ValueError("caractere fora do alfabeto base58")

        num = num * 58 + idx

    leading_zeros = len(value) - len(value.lstrip("1"))

    body = (
        num.to_bytes(
            (num.bit_length() + 7) // 8,
            "big",
        )
        if num
        else b""
    )

    return b"\x00" * leading_zeros + body


def is_valid_solana_address(value: str) -> bool:
    if not (32 <= len(value) <= 44):
        return False

    try:
        return len(b58decode(value)) == 32
    except ValueError:
        return False


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: Literal["solana"]
    mint: str = Field(
        min_length=32,
        max_length=44,
    )

    @field_validator("mint")
    @classmethod
    def _validate_mint(cls, v: str) -> str:
        if not is_valid_solana_address(v):
            raise ValueError(
                "mint deve ser um endereço Solana base58 válido (32 bytes)"
            )

        return v


class Intelligence(BaseModel):
    creator: Any | None = None

    # Compatibility:
    # - stub/legacy provider may expose a list
    # - Alpha Hunter bridge exposes a structured object
    wallet_activity: dict[str, Any] | list[Any] = Field(
        default_factory=list
    )

    concentration: Any | None = None

    # Compatibility:
    # - stub/legacy provider may expose a list
    # - Alpha Hunter bridge exposes a structured object
    behavioral_findings: dict[str, Any] | list[Any] = Field(
        default_factory=list
    )

    evidence: list[Any] = Field(
        default_factory=list
    )


class DataQuality(BaseModel):
    status: str
    limitations: list[str] = Field(
        default_factory=list
    )


class InvestigateResponse(BaseModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    chain: Literal["solana"]
    mint: str
    status: str
    generated_at: str
    intelligence: Intelligence
    data_quality: DataQuality