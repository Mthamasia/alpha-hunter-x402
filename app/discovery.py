from __future__ import annotations

from typing import Any

from app.config import Settings

CHALLENGE_TAG = "x402-global-challenge"


def bazaar_extension(settings: Settings) -> dict[str, Any]:
    """Bazaar declaration carried by the standard x402 PAYMENT-REQUIRED header."""
    return {
        "bazaar": {
            "info": {
                "title": "Alpha Hunter X - Autonomous On-Chain Intelligence",
                "description": (
                    "Paid behavioral on-chain intelligence for autonomous agents. "
                    "Submit a Solana token mint to receive structured creator, wallet, "
                    "behavioral, evidence, and data-quality findings."
                ),
                "tags": ["on-chain-intelligence", "solana", CHALLENGE_TAG],
                "input": {
                    "type": "http",
                    "method": "POST",
                    "bodyType": "json",
                    "body": {
                        "chain": "solana",
                        "mint": "So11111111111111111111111111111111111111112",
                    },
                },
                "output": {
                    "type": "json",
                    "example": {
                        "schema_version": "1.0",
                        "status": "STALE",
                        "data_quality": {
                            "status": "STALE",
                            "limitations": ["Example only; live data quality varies."],
                        },
                    },
                },
            }
        }
    }
