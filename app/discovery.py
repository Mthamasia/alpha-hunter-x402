from __future__ import annotations

from typing import Any

from app.config import Settings
from x402.extensions.bazaar import OutputConfig, declare_discovery_extension

CHALLENGE_TAG = "x402-global-challenge"


def bazaar_extension(settings: Settings) -> dict[str, Any]:
    """Official Bazaar declaration carried by PAYMENT-REQUIRED."""
    _ = settings
    extension = declare_discovery_extension(
        input={
            "chain": "solana",
            "mint": "So11111111111111111111111111111111111111112",
        },
        input_schema={
            "properties": {
                "chain": {"type": "string", "const": "solana"},
                "mint": {"type": "string", "minLength": 32, "maxLength": 44},
            },
            "required": ["chain", "mint"],
            "additionalProperties": False,
        },
        body_type="json",
        output=OutputConfig(
            example={
                "schema_version": "1.0",
                "status": "STALE",
                "data_quality": {
                    "status": "STALE",
                    "limitations": ["Example only; live data quality varies."],
                },
            }
        ),
    )
    extension["bazaar"]["info"]["tags"] = [
        "on-chain-intelligence",
        "solana",
        CHALLENGE_TAG,
    ]
    extension["bazaar"]["info"]["title"] = (
        "Alpha Hunter X - Autonomous On-Chain Intelligence"
    )
    extension["bazaar"]["info"]["description"] = (
        "Paid behavioral on-chain intelligence for autonomous agents."
    )
    return extension
