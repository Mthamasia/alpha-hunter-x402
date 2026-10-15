"""Reference agent flow: discover -> free preview -> decide -> inspect offer.

Standard library only. It NEVER signs or sends a payment: the last step
only decodes the x402 PAYMENT-REQUIRED offer so the agent (or a human
wallet) can decide. Plug your x402 wallet client in where indicated.

    python examples/agent_preview_then_pay.py https://<api-host> <SOLANA_MINT>
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from typing import Callable

Http = Callable[[str, str, dict | None], tuple[int, dict, dict]]


def urllib_http(base_url: str) -> Http:
    def call(method: str, path: str, body: dict | None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "X-Agent-Id": "ahx-reference-agent/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, dict(r.headers), json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read() or b"{}")
    return call


def decide(http: Http, mint: str) -> dict:
    status, _, manifest = http("GET", "/.well-known/x402", None)
    paid = next((r for r in manifest.get("resources", []) if r["path"] == "/v1/token/investigate"), None)
    out = {"discovered": status == 200 and paid is not None,
           "price_usd": paid and paid.get("price_usd")}

    status, _, preview = http("POST", "/v1/token/preview", {"chain": "solana", "mint": mint})
    if status == 429:
        return {**out, "decision": "WAIT", "why": "free preview quota exhausted"}
    if status != 200:
        return {**out, "decision": "SKIP", "why": f"preview unavailable (HTTP {status})"}
    out.update(status=preview["status"], premium_eligible=preview.get("premium_eligible"),
               reasons=preview.get("premium_ineligibility_reasons", []))
    if not preview.get("premium_eligible"):
        return {**out, "decision": "DO_NOT_PAY", "why": "no chargeable, verifiable intelligence now"}

    status, headers, _ = http("POST", "/v1/token/investigate", {"chain": "solana", "mint": mint})
    offer_b64 = {k.lower(): v for k, v in headers.items()}.get("payment-required")
    offer = json.loads(base64.b64decode(offer_b64)) if status == 402 and offer_b64 else None
    accepts = (offer or {}).get("accepts", [{}])[0]
    return {**out, "decision": "READY_TO_PAY",
            "offer": {"network": accepts.get("network"), "asset": accepts.get("asset"),
                      "amount_atomic": accepts.get("amount"), "pay_to": accepts.get("payTo")},
            "next": "sign the offer with your x402 wallet client and resend with PAYMENT-SIGNATURE"}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(decide(urllib_http(sys.argv[1]), sys.argv[2]), indent=2))
