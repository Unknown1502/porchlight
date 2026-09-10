"""Deterministic indicator extraction.

The model also extracts indicators, and the two are unioned. Regex catches what
a model paraphrases away; the model catches what regex cannot (a number spelled
out in words, an address described rather than written). Neither is trusted to
be complete on its own.
"""
from __future__ import annotations

import re

from ..models import Indicators

_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_DOMAIN = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|in|co|io|info|xyz|top|online|site|shop|club|live|link|app|gov|edu)\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# International shapes, loose on purpose; validation is a later concern. Country-
# specific plausibility lives in the jurisdiction pack, not here.
_PHONE = re.compile(r"(?:\+?\d{1,3}[ -]?)?(?:\(?\d{2,5}\)?[ -]?)?\d{3}[ -]?\d{3,5}\b")
_BTC = re.compile(r"\b(?:bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_ETH = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_UPI = re.compile(r"\b[\w.-]{2,64}@(?:ok\w+|paytm|ybl|apl|axl|ibl|upi|sbi|hdfcbank|icici|axisbank)\b", re.I)
# The keyword is matched case-insensitively; the reference itself is NOT. A
# case-insensitive capture here turns "close the case without an FIR" into a
# reference number called "without" — the keyword is ordinary English, so the
# shape of what follows is the only thing separating a real docket id from the
# next word in the sentence. Hence: upper-case/digit shape, and at least one
# digit somewhere in it.
_CASE = re.compile(
    r"\b(?i:case|ref(?:erence)?|complaint|fir|docket|ticket)\s*(?i:no\.?|number|#|id)?\s*[:\-]?\s*"
    r"((?=[A-Z0-9/-]*\d)[A-Z0-9][A-Z0-9/-]{4,24})\b"
)
_MASKED_ACCT = re.compile(r"\b(?:a/c|acct|account)\D{0,10}(?:x{2,}|\*{2,})?(\d{4})\b", re.I)

_MIN_PHONE_DIGITS = 8


def _clean_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) < _MIN_PHONE_DIGITS or len(digits) > 15:
        return None
    return raw.strip()


def extract_indicators(text: str) -> Indicators:
    urls = sorted({m.group(0).rstrip(".,);") for m in _URL.finditer(text)})
    emails = sorted({m.group(0) for m in _EMAIL.finditer(text)})
    upis = sorted({m.group(0) for m in _UPI.finditer(text)})

    # A domain already inside a URL or an email/UPI handle is not a separate finding.
    consumed = " ".join(urls + emails + upis).lower()
    domains = sorted({
        m.group(0).lower() for m in _DOMAIN.finditer(text)
        if m.group(0).lower() not in consumed
    })

    phones = []
    for m in _PHONE.finditer(text):
        # Skip anything already claimed by another indicator type.
        span = m.group(0)
        if span in consumed:
            continue
        c = _clean_phone(span)
        if c:
            phones.append(c)

    return Indicators(
        phone_numbers=sorted(set(phones)),
        urls=urls,
        domains=domains,
        email_addresses=emails,
        crypto_addresses=sorted({m.group(0) for m in _BTC.finditer(text)}
                                | {m.group(0) for m in _ETH.finditer(text)}),
        upi_ids=upis,
        bank_accounts_masked=sorted({f"****{m.group(1)}" for m in _MASKED_ACCT.finditer(text)}),
        case_or_reference_numbers=sorted({m.group(1) for m in _CASE.finditer(text)}),
    )


def merge_indicators(a: Indicators, b: Indicators) -> Indicators:
    """Union of two extractions, order-stable."""
    out = {}
    for field in Indicators.model_fields:
        out[field] = sorted(set(getattr(a, field)) | set(getattr(b, field)))
    return Indicators(**out)


def indicator_keys(ind: Indicators) -> set[str]:
    """Hard indicators usable for campaign matching.

    Deliberately excludes domains and emails on their own — a scam that name-drops
    ``sbi.co.in`` is not thereby linked to every other scam that does.
    """
    keys: set[str] = set()
    keys |= {f"phone:{_normalise_phone(p)}" for p in ind.phone_numbers}
    keys |= {f"upi:{u.lower()}" for u in ind.upi_ids}
    keys |= {f"crypto:{c}" for c in ind.crypto_addresses}
    keys |= {f"url:{u.lower()}" for u in ind.urls}
    keys |= {f"acct:{a}" for a in ind.bank_accounts_masked}
    return keys


def _normalise_phone(p: str) -> str:
    d = re.sub(r"\D", "", p)
    return d[-10:] if len(d) >= 10 else d
