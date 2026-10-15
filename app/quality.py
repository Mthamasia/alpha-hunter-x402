"""Paid-result quality gate and public-response sanitization.

Two separate responsibilities, both pure functions (zero network, zero
Helius, zero database):

1. ``assess_premium_eligibility`` decides whether a provider result is
   good enough to be SOLD (x402 Mainnet settlement). It does not trust the
   bridge's own freshness label alone: at least one evidence item must be a
   verifiable Solana transaction reference, explicitly linked to the
   requested mint, with an observed on-chain timestamp that is itself recent
   relative to this server's clock. A report can be FRESH by the bridge's
   metadata and still be ineligible here.

2. ``sanitize_intelligence`` shapes what a buyer is allowed to see, in every
   payment mode: internal/sensitive keys are dropped, a creator reputation
   without observed outcomes is reported as UNKNOWN, and anomalous or
   contradictory market-cap values are removed and flagged.

Ineligibility reasons are stable public codes (no internal detail), so the
free preview can tell an agent *before* it pays whether the premium call
would be chargeable.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.models import Intelligence, b58decode

# ------------------------------------------------------------------ reasons
REASON_NOT_FRESH = "data_not_fresh"
REASON_NO_VERIFIABLE_TX = "no_verifiable_transaction_evidence"
REASON_NOT_LINKED_TO_MINT = "evidence_not_linked_to_requested_mint"
REASON_NO_TIMESTAMP = "evidence_without_onchain_timestamp"
REASON_TOO_OLD = "evidence_older_than_freshness_window"
REASON_FUTURE = "evidence_timestamp_in_future"
REASON_NO_FINDING = "no_observed_behavioral_finding"
REASON_NO_EXPLANATION = "no_behavioral_explanation"

# Solana mainnet beta genesis (2020-03-16). Earlier timestamps are invalid.
SOLANA_GENESIS_UNIX = 1_584_368_940

SIGNATURE_KEYS = ("transaction_signature", "tx_signature", "signature")
URL_KEYS = ("url", "reference", "explorer_url")
TIME_KEYS = ("block_time", "blockTime", "timestamp", "observed_at")
MINT_KEYS = ("mint", "token_mint")
EXPLORER_HOSTS = frozenset(
    {"solscan.io", "explorer.solana.com", "solana.fm", "orb.helius.dev", "xray.helius.xyz"}
)


def is_valid_solana_signature(value: Any) -> bool:
    """A Solana transaction signature is base58 of exactly 64 bytes."""
    if not isinstance(value, str) or not (64 <= len(value) <= 90):
        return False
    try:
        return len(b58decode(value)) == 64
    except ValueError:
        return False


def _signature_from_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in EXPLORER_HOSTS:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) == 2 and parts[0] == "tx" and is_valid_solana_signature(parts[1]):
        return parts[1]
    return None


def evidence_signature(item: Any) -> str | None:
    """Return the verifiable transaction signature of an evidence item."""
    if not isinstance(item, dict):
        return None
    for key in SIGNATURE_KEYS:
        if is_valid_solana_signature(item.get(key)):
            return item[key]
    for key in URL_KEYS:
        sig = _signature_from_url(item.get(key))
        if sig:
            return sig
    return None


def evidence_unix_time(item: Any) -> int | None:
    """Observed on-chain time of an evidence item, in unix seconds."""
    if not isinstance(item, dict):
        return None
    for key in TIME_KEYS:
        value = item.get(key)
        if type(value) is int:
            return value
        if type(value) is float and math.isfinite(value):
            return int(value)
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                # A naive timestamp has no verifiable chronology.
                continue
            return int(parsed.timestamp())
    return None


def evidence_linked_to_mint(item: Any, mint: str) -> bool:
    if not isinstance(item, dict):
        return False
    return any(item.get(key) == mint for key in MINT_KEYS)


def substantive_finding(intelligence: Any) -> bool:
    """Observed outcomes, not a profile stub or an unexplained score."""
    creator = getattr(intelligence, "creator", None)
    if isinstance(creator, dict) and _creator_observed_outcomes(creator):
        return True

    wallet = getattr(intelligence, "wallet_activity", None)
    if isinstance(wallet, dict):
        profile = wallet.get("profile")
        if isinstance(profile, dict):
            count = profile.get("total_events")
            if type(count) is int and count > 0:
                return True
        events = wallet.get("recent_events")
        if isinstance(events, list) and any(isinstance(i, dict) and i for i in events):
            return True

    findings = getattr(intelligence, "behavioral_findings", None)
    if isinstance(findings, dict):
        alerts = findings.get("alerts")
        if isinstance(alerts, list) and any(isinstance(i, dict) and i for i in alerts):
            return True
    # A free-text explanation alone is never an observation.
    return False


def has_explanation(intelligence: Any) -> bool:
    findings = getattr(intelligence, "behavioral_findings", None)
    if not isinstance(findings, dict):
        return False
    explanation = findings.get("explanation")
    return isinstance(explanation, str) and bool(explanation.strip())


def _creator_observed_outcomes(creator: dict) -> bool:
    profile = creator.get("profile")
    if not isinstance(profile, dict):
        return False
    for key in ("verified_launches", "confirmed_outcomes", "observed_launches"):
        n = profile.get(key)
        if type(n) is int and n > 0:
            return True
    return False


@dataclass
class Eligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    newest_evidence_age_seconds: int | None = None


def assess_premium_eligibility(
    result: Any,
    mint: str,
    *,
    now: datetime | None = None,
    max_evidence_age_seconds: int,
    clock_skew_seconds: int = 300,
) -> Eligibility:
    """Fail-closed: anything not proven is a reason not to charge."""
    now_ts = int((now or datetime.now(timezone.utc)).timestamp())
    reasons: list[str] = []

    quality = getattr(getattr(result, "data_quality", None), "status", None)
    if quality != "FRESH":
        reasons.append(REASON_NOT_FRESH)

    intelligence = getattr(result, "intelligence", None)
    evidence = getattr(intelligence, "evidence", None)
    if not isinstance(evidence, list):
        evidence = []

    verifiable = [i for i in evidence if evidence_signature(i)]
    linked = [i for i in verifiable if evidence_linked_to_mint(i, mint)]
    newest_age: int | None = None

    if not verifiable:
        reasons.append(REASON_NO_VERIFIABLE_TX)
    elif not linked:
        reasons.append(REASON_NOT_LINKED_TO_MINT)
    else:
        times = [evidence_unix_time(i) for i in linked]
        valid = [t for t in times if t is not None and t >= SOLANA_GENESIS_UNIX]
        if not valid:
            reasons.append(REASON_NO_TIMESTAMP)
        else:
            future = [t for t in valid if t > now_ts + clock_skew_seconds]
            plausible = [t for t in valid if t <= now_ts + clock_skew_seconds]
            if future and not plausible:
                reasons.append(REASON_FUTURE)
            else:
                newest = max(plausible)
                newest_age = max(0, now_ts - newest)
                if newest_age > max_evidence_age_seconds:
                    reasons.append(REASON_TOO_OLD)

    if intelligence is None or not substantive_finding(intelligence):
        reasons.append(REASON_NO_FINDING)
    elif not has_explanation(intelligence):
        reasons.append(REASON_NO_EXPLANATION)

    return Eligibility(eligible=not reasons, reasons=reasons, newest_evidence_age_seconds=newest_age)


# ------------------------------------------------------------ sanitization
_SENSITIVE_KEY = re.compile(
    r"(secret|password|passwd|api[_-]?key|private[_-]?key|seed[_-]?phrase|^seed$|mnemo|"
    r"service[_-]?token|bridge[_-]?token|auth[_-]?token|access[_-]?token|"
    r"credential|db[_-]?path|database|sqlite|^internal|^_)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(^/(home|root|var|opt|srv|etc|tmp)/|\.db(-wal|-shm)?$|sqlite:|"
    r"\b(?:127\.0\.0\.1|localhost)\b|\b(?:\d{1,3}\.){3}\d{1,3}\b)",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"
MARKETCAP_KEYS = frozenset(
    {"marketcap", "market_cap", "market_cap_usd", "marketcap_usd", "fdv", "fdv_usd"}
)
_REPUTATION_KEYS = ("reputation", "reputation_score", "trust_score", "trust_level", "reputation_label")


def _strip_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_sensitive(v)
            for k, v in value.items()
            if not (isinstance(k, str) and _SENSITIVE_KEY.search(k))
        }
    if isinstance(value, list):
        return [_strip_sensitive(v) for v in value]
    if isinstance(value, str) and _SENSITIVE_VALUE.search(value):
        return _REDACTED
    return value


def _collect_marketcaps(value: Any, out: list[tuple[dict, str]]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in MARKETCAP_KEYS:
                out.append((value, k))
            else:
                _collect_marketcaps(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_marketcaps(v, out)


def _clean_marketcaps(tree: dict, max_marketcap_usd: float, max_ratio: float) -> list[str]:
    refs: list[tuple[dict, str]] = []
    _collect_marketcaps(tree, refs)
    limitations: list[str] = []
    valid: list[tuple[dict, str, float]] = []
    anomalous = 0
    for parent, key in refs:
        v = parent[key]
        if v is None:
            continue
        ok = type(v) in (int, float) and math.isfinite(v) and 0 < v <= max_marketcap_usd
        if ok:
            valid.append((parent, key, float(v)))
        else:
            parent[key] = None
            anomalous += 1
    if anomalous:
        limitations.append(
            f"{anomalous} anomalous market-cap value(s) removed (non-numeric, non-positive "
            f"or above {max_marketcap_usd:,.0f} USD)."
        )
    if len(valid) >= 2:
        lo = min(v for _, _, v in valid)
        hi = max(v for _, _, v in valid)
        if hi / lo > max_ratio:
            for parent, key, _ in valid:
                parent[key] = None
            limitations.append(
                "Contradictory market-cap values for this token were removed; "
                "market data is not reliable for this report."
            )
    return limitations


def _normalize_reputation(creator: Any) -> list[str]:
    if not isinstance(creator, dict) or _creator_observed_outcomes(creator):
        return []
    touched = False
    for holder in (creator, creator.get("profile")):
        if not isinstance(holder, dict):
            continue
        for key in _REPUTATION_KEYS:
            if key in holder:
                holder.pop(key)
                touched = True
    creator["reputation"] = "UNKNOWN"
    if touched:
        return ["Creator reputation reported as UNKNOWN: no observed launch outcomes support a score."]
    return ["Creator reputation is UNKNOWN: no observed launch outcomes."]


MARKET_NUMERIC_KEYS = MARKETCAP_KEYS | {"liquidity", "liquidity_usd", "volume", "volume_usd", "price", "price_usd"}
MARKET_TIME_KEYS = ("updated_at_unix", "updated_at", "market_data_updated_at")
MODEL_KEYS = ("scores", "decision", "alpha_score", "risk_score", "confidence")


def _market_dicts(value: Any, out: list[dict]) -> None:
    if isinstance(value, dict):
        if any(isinstance(k, str) and k.lower() in MARKETCAP_KEYS for k in value):
            out.append(value)
        for v in value.values():
            _market_dicts(v, out)
    elif isinstance(value, list):
        for v in value:
            _market_dicts(v, out)


def _market_time(d: dict) -> int | None:
    for key in MARKET_TIME_KEYS:
        t = evidence_unix_time({"timestamp": d.get(key)}) if isinstance(d.get(key), str) else d.get(key)
        if type(t) is int and t >= SOLANA_GENESIS_UNIX:
            return t
    return None


def _label_market(tree: dict, now_ts: int, current_window: int) -> list[str]:
    """Market data must carry its origin and update time; otherwise it is
    removed. Old market data is kept but labeled historical."""
    found: list[dict] = []
    _market_dicts(tree, found)
    removed = 0
    notes: list[str] = []
    for d in found:
        t = _market_time(d)
        if not d.get("source") or t is None:
            for k in list(d):
                if isinstance(k, str) and k.lower() in MARKET_NUMERIC_KEYS:
                    d[k] = None
            removed += 1
            continue
        d["data_age_seconds"] = max(0, now_ts - t)
        d["freshness"] = "current" if now_ts - t <= current_window else "historical"
    if removed:
        notes.append(f"{removed} market data block(s) without source or update time removed.")
    if any(d.get("freshness") == "historical" for d in found):
        notes.append("Market data is historical, not current.")
    return notes


def _separate_model_outputs(raw: dict) -> list[str]:
    findings = raw.get("behavioral_findings")
    if not isinstance(findings, dict):
        return []
    model = findings.get("model_scores") if isinstance(findings.get("model_scores"), dict) else {}
    moved = False
    for key in MODEL_KEYS:
        if key in findings:
            model[key] = findings.pop(key)
            moved = True
    if model:
        model["kind"] = "model_output"
        model.setdefault("note", "Model outputs, not on-chain observations.")
        findings["model_scores"] = model
    return ["Scores and decisions are model outputs, not observations."] if moved or model else []


def _label_evidence(raw: dict) -> None:
    for item in raw.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        sig = evidence_signature(item)
        confirmed = sig and item.get("onchain_verified") is True and item.get("verified_at")
        item["verification"] = (
            "onchain_confirmed" if confirmed else "recorded_not_reverified" if sig else "no_onchain_reference"
        )
        if sig and not item.get("explorer_url"):
            item["explorer_url"] = "https://solscan.io/tx/" + sig


def sanitize_intelligence(
    intelligence: Intelligence,
    *,
    max_marketcap_usd: float = 1e11,
    max_marketcap_ratio: float = 10.0,
    current_market_window_seconds: int = 3600,
) -> tuple[Intelligence, list[str]]:
    """Return a public-safe copy of ``intelligence`` plus added limitations."""
    raw = _strip_sensitive(intelligence.model_dump(mode="json"))
    limitations: list[str] = []
    if raw.get("creator") is not None:
        limitations += _normalize_reputation(raw["creator"])
    limitations += _clean_marketcaps(raw, max_marketcap_usd, max_marketcap_ratio)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    limitations += _label_market(raw, now_ts, current_market_window_seconds)
    limitations += _separate_model_outputs(raw)
    _label_evidence(raw)
    if raw.get("evidence"):
        limitations.append(
            "Evidence items are recorded observations; verify each signature on a Solana "
            "explorer unless marked onchain_confirmed."
        )
    return Intelligence(**raw), limitations
