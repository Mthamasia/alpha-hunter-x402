"""Builders for structurally valid on-chain evidence used in tests.

These are SYNTHETIC values with the exact shape of real Solana data (a
64-byte base58 transaction signature, a unix block time). They prove the
gate's logic; they are not evidence of real-world data quality.
"""
from __future__ import annotations

import hashlib
import time

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    num = int.from_bytes(raw, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _ALPHABET[rem] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + out


def signature(seed: str = "ahx") -> str:
    """Deterministic, well-formed (64-byte base58) Solana signature."""
    return b58encode(hashlib.sha512(seed.encode()).digest())


def tx_evidence(mint: str, *, age_seconds: int = 120, seed: str = "ahx", **extra) -> dict:
    item = {
        "type": "transaction",
        "mint": mint,
        "transaction_signature": signature(seed),
        "block_time": int(time.time()) - age_seconds,
    }
    item.update(extra)
    return item
