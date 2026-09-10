"""Defensive pre-processing applied before any model sees a report.

This is belt and braces. The authoritative control is the policy engine at the
gateway; these are cheap heuristics that (a) strip identifiers we must never
store and (b) give us a deterministic injection signal we can assert on in tests
without paying for a model call.
"""
from __future__ import annotations

import re
import unicodedata

# Characters used to break up a trigger phrase without changing how it reads.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

# Homoglyphs that survive NFKC because they are genuinely distinct letters.
# Kept to the substitutions actually seen in the wild for Latin script rather
# than a full confusables table, which would be large and mostly dead weight.
_HOMOGLYPHS = str.maketrans({
    "ı": "i",  # dotless i
    "а": "a", "е": "e", "о": "o", "р": "p",
    "с": "c", "х": "x", "у": "y", "і": "i",
    "ο": "o", "α": "a", "А": "A", "О": "O",
})


def normalise_for_matching(text: str) -> str:
    """Fold the cheap evasions before pattern matching.

    Not cosmetic. A detector that reads "ig<zwsp>nore all pre<zwsp>vious
    instructions" as unremarkable prose is not a weak detector, it is a broken
    one — the evasion costs an attacker one find-and-replace. Compatibility
    normalisation, zero-width removal, homoglyph folding and whitespace
    collapsing between single letters cover the mechanical tricks; they do not
    and cannot cover paraphrase, encoding or another language, which is why this
    layer is a flagger and P001-P005 are the controls.

    Returned text is for *matching only*. Everything stored, shown or sent keeps
    the original bytes — normalising the artefact itself would destroy evidence.
    """
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_ZERO_WIDTH).translate(_HOMOGLYPHS)
    # "d o   n o t" -> "do not": collapse runs of single letters separated by
    # spaces. Guarded to runs of 4+ so ordinary prose and initialisms survive.
    out = re.sub(r"\b(?:[A-Za-z][ \t]{1,3}){3,}[A-Za-z]\b",
                 lambda m: re.sub(r"[ \t]+", "", m.group(0)), out)
    return out


# Identifiers that must never be persisted or echoed.
_PATTERNS = [
    # Card numbers (13-19 digits, optionally separated). Keep last 4.
    # The class here is [^0-9], not 'D': a bare 'D' strips the letter D and
    # leaves the separators in, so a card written "4111 1111 1111 111 1" was
    # redacted to "[CARD-****11 1]" — a mangled tail that is neither the last
    # four digits nor obviously wrong on screen.
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
     lambda m: f"[CARD-****{re.sub(r'[^0-9]', '', m.group(0))[-4:]}]"),
    # 12-digit national identity shape (e.g. India's Aadhaar), grouped 4-4-4.
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), lambda m: "[GOVT-ID-REDACTED]"),
    # US SSN
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), lambda m: "[GOVT-ID-REDACTED]"),
    # OTP-ish: "OTP is 123456"
    (re.compile(r"\b(?:OTP|otp|one[- ]time (?:password|code)|PIN)\D{0,12}(\d{4,8})\b"),
     lambda m: m.group(0).replace(m.group(1), "[OTP-REDACTED]")),
]

# Phrases that only make sense if the author is addressing an automated reader.
_INJECTION_SIGNATURES = [
    r"ignore (?:all )?(?:your |the )?(?:previous |prior |above )?instructions",
    r"disregard (?:all )?(?:previous|prior|the above)",
    r"\bsystem\s*(?:prompt|message|note|override)\b",
    r"you are (?:now |actually )?(?:a|an) \w+",
    r"\bmark (?:this|it|the (?:report|case|ticket)) (?:as )?(?:resolved|closed|safe|benign|false[- ]positive)\b",
    r"\bmark resolved\b|\bclose the (?:ticket|case|report)\b",
    r"\b(?:do not|don'?t) (?:notify|alert|report|escalate|flag)\b",
    r"\bthis (?:report|message) is a (?:test|drill|false alarm)\b",
    r"\bassistant\s*:\s*",
    r"<\|.*?\|>",
    r"\[/?INST\]",
    r"\bnew instructions?\b",
    r"\boverride\b.{0,20}\bpolicy\b",
    r"\bprevious (?:rules|constraints) (?:no longer|do not) apply\b",
    # Graph poisoning — instructions aimed at the correlation layer.
    r"\b(?:do not|don'?t) cluster\b",
    r"\bassign this report to campaign\b",
    r"\bmerge all (?:other )?campaigns\b",
    r"\bset script_fingerprint\b",
    r"\bthis report is unrelated to any other\b",
    # Forged authority — an approval asserted from inside the data.
    r"\bAPPROVAL_TOKEN\s*[=:]",
    r"\bauto[- ]approve\b",
    r"\byou are (?:now )?authoris?zed to send\b",
    r"\bpre[- ]approved by\b",
    r"\bmessage from the (?:coalition )?coordinator\b",
    r"\bproceed to send\b",
    # Exfiltration phrasing that only makes sense addressed to a machine.
    r"\b(?:reply|respond) to this (?:email|message) with the (?:full )?list\b",
    r"\bPOST the (?:case|report) file\b",
    r"\bforward all (?:prior )?reports\b",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _INJECTION_SIGNATURES]

# The same signatures with their literal spaces made optional. Letter-spacing
# ("d o   n o t   e s c a l a t e") normalises to a single run with the spaces
# gone, which the space-bearing patterns above would then miss — so the fold and
# the matcher have to agree about whitespace. Deriving this set from the same
# source list means a new signature cannot be added to one and forgotten in the
# other.
_INJECTION_RE_NOSPACE = [
    re.compile(p.replace(" ", r"\s*"), re.IGNORECASE | re.DOTALL)
    for p in _INJECTION_SIGNATURES
]


def scrub_pii(text: str) -> str:
    """Remove identifiers that must never be stored, keeping the text readable."""
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def detect_injection(text: str) -> tuple[bool, list[str]]:
    """Deterministic prompt-injection signal.

    Returns ``(found, evidence_snippets)``. This never blocks processing on its
    own — a report containing an injection attempt is still a real report, and
    often a more interesting one. It flags the case, feeds the red-team suite,
    and is recorded in the audit trail.
    """
    probe = normalise_for_matching(text)
    hits: list[str] = []
    for rx in _INJECTION_RE_NOSPACE:
        for m in rx.finditer(probe):
            snippet = probe[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
            hits.append(snippet.strip())
            if len(hits) >= 6:
                return True, hits
    return bool(hits), hits


# What a hostile report is asking the machine to *do*, as opposed to what it is
# telling the victim. Each entry maps a request shape to the policy action that
# would have to be permitted for the request to succeed.
_REQUESTED_CAPABILITY = [
    (re.compile(r"\b(?:visit|open|go to|download|confirm receipt by (?:visiting|opening))\b"
                r"|\bverify (?:this|it) is genuine by\b", re.I), "http.fetch"),
    (re.compile(r"\b(?:send|broadcast|instruct all residents|approve and send)\b.{0,60}"
                r"\b(?:sms|message|broadcast|residents?|list)\b", re.I), "broadcast.send"),
    (re.compile(r"\b(?:reply|respond) to this (?:email|message) with\b"
                r"|\bforward all (?:prior )?reports\b"
                r"|\bPOST the (?:case|report) file\b", re.I), "email.send"),
    (re.compile(r"\b(?:call|phone|ring)\b.{0,40}\b(?:our|the) (?:office|verification desk|helpline)\b",
                re.I), "voice.call"),
]


def requested_capabilities(text: str) -> list[str]:
    """Policy actions a hostile report is trying to talk the system into.

    This does **not** decide to perform anything. It names what the text asked
    for, so that the request can be put to the policy engine as a question and
    the refusal can be written down against the report that made it. A denial
    nobody can point at is not a control a coordinator can trust.
    """
    probe = normalise_for_matching(text)
    return sorted({action for rx, action in _REQUESTED_CAPABILITY if rx.search(probe)})
