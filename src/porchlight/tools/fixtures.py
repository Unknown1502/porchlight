"""Fixture-backed corroboration data.

Why the demo runs on fixtures rather than live feeds, by default:

1. **A demo must not depend on a third party being up.** URLhaus or RDAP being
   slow at the wrong moment turns the corroboration beat into a spinner.
2. **The corpus is synthetic.** Every domain in it ends in `.example`, which is
   reserved and will never resolve. Querying a live feed about it returns
   "unknown" every time — which is honest, and also shows the judge nothing.
3. **Reproducibility.** The eval must produce the same number twice.

What this is *not*: a stub pretending an integration exists. The live path is
real, is exercised by the same code, and is one environment variable away
(`PORCHLIGHT_FIXTURE_TOOLS=0`). Every fixture-backed result carries
``source="fixture"`` and ``fixture_backed=True`` so the UI can badge it and a
judge is never shown a synthetic verdict dressed as a live lookup.

Verdicts here are assertions about invented identifiers. They are internally
consistent with the corpus generator's crews; they are not claims about anything
in the real world.
"""
from __future__ import annotations

import re
from typing import Any

# Domains the corpus crews use. All under the reserved .example TLD (RFC 2606).
# "Registered days ago" is the strongest cheap signal a real deployment would
# get from RDAP, so the fixtures model that rather than a flat verdict.
_DOMAIN_FIXTURES: dict[str, dict[str, Any]] = {
    "verify-kyc.example":      {"verdict": "malicious", "age_days": 6},
    "verify-customs.example":  {"verdict": "malicious", "age_days": 11},
    "verify-parcel.example":   {"verdict": "malicious", "age_days": 4},
    "verify-refund.example":   {"verdict": "malicious", "age_days": 19},
    "verify-sbi.example":      {"verdict": "malicious", "age_days": 3},
    "verify-gov.example":      {"verdict": "suspicious", "age_days": 74},
    "verify-secure.example":   {"verdict": "suspicious", "age_days": 121},
    "verify-billing.example":  {"verdict": "suspicious", "age_days": 96},
    "verify-crew0.example":    {"verdict": "malicious", "age_days": 5},
    "verify-decoy.example":    {"verdict": "malicious", "age_days": 8},
    # A legitimate domain a scam might name-drop. Present on purpose: the
    # challenge set needs a case where shared infrastructure is innocent.
    "example.com":             {"verdict": "benign", "age_days": 10957},
}

# Phone prefixes the corpus assigns to crews, with what a reputation feed would
# plausibly say. Keyed on the normalised (digits-only) form.
_PHONE_FIXTURES: list[tuple[str, dict[str, Any]]] = [
    (r"^9000000(0[0-9]|1[0-9])$", {"verdict": "malicious",
                                   "detail": "reported as a scam callback by multiple communities"}),
    (r"^9000000\d{3}$", {"verdict": "suspicious",
                         "detail": "recently seen in fraud reports; low call volume, high report rate"}),
]

_UPI_FIXTURES = re.compile(r"^payee\d{1,3}@ybl$", re.IGNORECASE)


def enabled() -> bool:
    from ..config import settings
    return settings().use_fixture_tools


def _label(payload: dict[str, Any]) -> dict[str, Any]:
    """Every fixture result says so, in the payload, always."""
    payload["source"] = "fixture"
    payload["fixture_backed"] = True
    payload["disclaimer"] = "synthetic corroboration data; not a live feed lookup"
    return payload


def lookup_domain(domain: str) -> dict[str, Any] | None:
    domain = re.sub(r"^https?://", "", domain.strip().lower()).split("/")[0]
    hit = _DOMAIN_FIXTURES.get(domain)
    if hit is None:
        # An unknown .example domain is still known to be non-resolvable, which
        # is a real finding: it cannot be the bank it claims to be.
        if domain.endswith(".example"):
            return _label({
                "indicator": domain, "verdict": "suspicious", "age_days": None,
                "detail": "reserved .example domain — cannot be a real institution",
            })
        return None
    age = hit["age_days"]
    return _label({
        "indicator": domain, "verdict": hit["verdict"], "age_days": age,
        "detail": (f"registered {age} days ago — freshly created infrastructure"
                   if age is not None and age <= 30
                   else f"registered {age} days ago"),
    })


def lookup_url(url: str) -> dict[str, Any] | None:
    host = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0]
    hit = lookup_domain(host)
    if hit is None:
        return None
    verdict = hit["verdict"]
    return _label({
        "indicator": url,
        "verdict": verdict,
        "detail": ("listed in the community abuse feed" if verdict == "malicious"
                   else hit.get("detail", "no listing")),
    })


def lookup_phone(number: str) -> dict[str, Any] | None:
    digits = re.sub(r"\D", "", number)
    normalised = digits[-10:] if len(digits) >= 10 else digits
    for pattern, payload in _PHONE_FIXTURES:
        if re.match(pattern, normalised):
            return _label({"indicator": number, **payload})
    return None


def lookup_payment_handle(handle: str) -> dict[str, Any] | None:
    """Beneficiary handle check — the analogue of a bank's mule-account list."""
    if _UPI_FIXTURES.match(handle.strip()):
        return _label({
            "indicator": handle, "verdict": "malicious",
            "detail": ("beneficiary handle appears on the partner bank's suspect list; "
                       "opened recently, high inbound velocity, no outbound history"),
        })
    return None
