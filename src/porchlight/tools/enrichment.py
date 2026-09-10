"""Allow-listed enrichment tools.

Every function here is a Strands ``@tool``. Three properties matter more than
what they return:

1. **The allow-list is the boundary.** These are the only ways an indicator from
   a report gets touched. The agent has no general fetch tool, so "visit the link
   and tell me what it says" is not a capability it can be talked into.
2. **They never contact the indicator.** RDAP asks a registry about a name;
   URLhaus asks a database about a string. Nothing here resolves, connects to, or
   requests the scammer's infrastructure — which would leak the coalition's IP
   and tip the crew off.
3. **They fail open to `unknown`, never to `benign`.** A feed timing out must not
   look like a clean bill of health.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx
from strands import tool

from ..config import active_pack, settings

_TIMEOUT = httpx.Timeout(6.0, connect=3.0)
_UA = {"User-Agent": "porchlight-coalition-agent/0.1 (+defensive; contact: coalition coordinator)"}


def _unknown(indicator: str, reason: str) -> dict[str, Any]:
    return {"indicator": indicator, "verdict": "unknown", "source": "none", "detail": reason}


@tool
def check_url_reputation(url: str) -> dict:
    """Check a URL or domain against public abuse feeds.

    Does NOT visit the URL. Queries the URLhaus community database for a match.

    Args:
        url: The URL or domain exactly as it appeared in the report.

    Returns:
        A dict with keys: indicator, verdict (malicious|suspicious|unknown|benign),
        source, detail.
    """
    s = settings()
    if not s.urlhaus_key:
        return _unknown(url, "URLHAUS_AUTH_KEY not configured; no feed consulted")
    try:
        r = httpx.post(
            "https://urlhaus-api.abuse.ch/v1/url/",
            data={"url": url},
            headers={**_UA, "Auth-Key": s.urlhaus_key},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001 - a feed failure must not stop triage
        return _unknown(url, f"feed unavailable: {type(exc).__name__}")

    if data.get("query_status") == "ok":
        return {
            "indicator": url,
            "verdict": "malicious",
            "source": "urlhaus",
            "detail": f"listed {data.get('date_added', '?')}; threat={data.get('threat', '?')}",
        }
    if data.get("query_status") == "no_results":
        return _unknown(url, "not present in URLhaus (absence is not evidence of safety)")
    return _unknown(url, f"feed status {data.get('query_status')}")


@tool
def domain_age_days(domain: str) -> dict:
    """Look up how many days ago a domain was registered, via RDAP.

    A domain registered days ago that claims to be a long-established bank or
    government service is one of the strongest cheap signals available.

    Args:
        domain: A bare domain name, e.g. "verify-account.example".

    Returns:
        A dict with keys: indicator, verdict, source, detail, age_days.
    """
    domain = re.sub(r"^https?://", "", domain.strip().lower()).split("/")[0]
    if not re.fullmatch(r"[a-z0-9.-]{3,253}", domain):
        return {**_unknown(domain, "not a syntactically valid domain"), "age_days": None}
    try:
        r = httpx.get(f"https://rdap.org/domain/{domain}", headers=_UA, timeout=_TIMEOUT,
                      follow_redirects=True)
        if r.status_code == 404:
            return {**_unknown(domain, "no RDAP record (unregistered or unsupported TLD)"),
                    "age_days": None}
        r.raise_for_status()
        events = r.json().get("events", [])
    except Exception as exc:  # noqa: BLE001
        return {**_unknown(domain, f"RDAP unavailable: {type(exc).__name__}"), "age_days": None}

    reg = next((e for e in events if e.get("eventAction") == "registration"), None)
    if not reg:
        return {**_unknown(domain, "RDAP record has no registration event"), "age_days": None}
    try:
        when = datetime.fromisoformat(reg["eventDate"].replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return {**_unknown(domain, "unparseable registration date"), "age_days": None}

    age = (datetime.now(timezone.utc) - when).days
    if age <= 30:
        verdict, detail = "malicious", f"registered {age} days ago — freshly created infrastructure"
    elif age <= 180:
        verdict, detail = "suspicious", f"registered {age} days ago"
    else:
        verdict, detail = "benign", f"registered {age} days ago"
    return {"indicator": domain, "verdict": verdict, "source": "rdap",
            "detail": detail, "age_days": age}


@tool
def phone_shape(number: str) -> dict:
    """Sanity-check the shape of a phone number. Never dials it.

    Args:
        number: The callback number exactly as it appeared in the report.

    Returns:
        A dict with keys: indicator, verdict, source, detail.
    """
    digits = re.sub(r"\D", "", number)
    notes = []
    # E.164 is the only universal rule: 1-15 digits. Everything narrower is a
    # local convention and lives in the jurisdiction pack, so this tool works the
    # same in a country nobody has written a pack for yet.
    if len(digits) < 8:
        notes.append("too short to be a dialable number as written")
    if len(digits) > 15:
        notes.append("longer than E.164 permits — likely a concatenation")
    for shape in active_pack().phone_shapes:
        pattern, label = shape.get("pattern"), shape.get("label", "known local shape")
        if pattern and re.fullmatch(pattern, digits):
            notes.append(f"matches {label}")
            break
    if re.search(r"(\d)\1{5,}", digits):
        notes.append("long digit run — often a spoofed or display-only number")
    verdict = "suspicious" if any("spoof" in n or "too short" in n or "longer" in n for n in notes) else "unknown"
    return {"indicator": number, "verdict": verdict, "source": "phone_shape",
            "detail": "; ".join(notes) or "no shape anomalies detected"}
