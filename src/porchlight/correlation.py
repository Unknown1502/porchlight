"""Deterministic campaign clustering.

The model does not do the clustering. It *judges* a cluster this module builds.
That split matters for three reasons: the clustering is reproducible and
testable, the eval harness can score it without model variance, and a
prompt-injected report cannot talk its way into or out of a cluster — set
membership is computed from indicators, not from prose.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from rapidfuzz import fuzz
from strands import tool

from .config import settings
from .tools.store import iter_records

_FINGERPRINT_SIM_THRESHOLD = 88  # rapidfuzz token_set_ratio

# Fingerprints that carry no information. A cluster must never be built on one:
# "we could not classify these two reports" is not evidence they share a crew.
SENTINEL_FINGERPRINTS = {"", "unclassified script", "unknown", "scam call", "fraud attempt"}

# A fingerprint-only link is weak evidence, so it is fenced in hard: same area,
# inside this many days, and only between reports that carry no hard indicator
# of their own. Hard indicators are what actually make a cluster.
_FP_LINK_MAX_DAYS = 5
_FP_LINK_MAX_CLUSTER = 8


@dataclass
class Cluster:
    members: list[dict[str, Any]] = field(default_factory=list)
    shared_indicators: set[str] = field(default_factory=set)
    fingerprints: set[str] = field(default_factory=set)

    @property
    def report_ids(self) -> list[str]:
        return [m["report_id"] for m in self.members]

    @property
    def distinct_reporters(self) -> int:
        return len({m.get("reporter_pseudonym", m["report_id"]) for m in self.members})

    @property
    def areas(self) -> list[str]:
        return sorted({m.get("reporter_area", "") for m in self.members if m.get("reporter_area")})

    @property
    def first_seen(self) -> datetime:
        return min(_ts(m) for m in self.members)

    @property
    def last_seen(self) -> datetime:
        return max(_ts(m) for m in self.members)

    @property
    def campaign_id(self) -> str:
        basis = "|".join(sorted(self.shared_indicators)) or "|".join(sorted(self.fingerprints))
        return "cmp_" + hashlib.sha256(basis.encode()).hexdigest()[:10]

    def confidence(self) -> float:
        """Evidence-weighted, capped. Hard indicators dominate; volume alone does not."""
        score = 0.0
        if self.shared_indicators:
            score += 0.55 + min(0.15, 0.05 * (len(self.shared_indicators) - 1))
        if len(self.fingerprints) == 1 and len(self.members) >= 2:
            score += 0.20
        if self.distinct_reporters >= 3:
            score += 0.10
        if self.distinct_reporters >= 5:
            score += 0.05
        if len(self.areas) == 1 and len(self.members) >= 3:
            score += 0.05  # one pincode => a list is being worked
        span_days = max((self.last_seen - self.first_seen).days, 0)
        if span_days <= 7 and len(self.members) >= 3:
            score += 0.05
        return round(min(score, 0.97), 2)


def _ts(rec: dict[str, Any]) -> datetime:
    try:
        t = datetime.fromisoformat(str(rec["received_at"]))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


def _fingerprints_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    return fuzz.token_set_ratio(a, b) >= _FINGERPRINT_SIM_THRESHOLD


# An indicator carried by more reports than this is describing infrastructure
# rather than a crew, whatever else is true about it.
_MAX_REPORTS_PER_INDICATOR = 12


def _link_is_corroborated(a: dict[str, Any], b: dict[str, Any],
                          keys_a: set[str], keys_b: set[str]) -> bool:
    """Is there enough evidence to say these two reports share a crew?

    Two ways to qualify, and one shared indicator alone is not one of them:

    * **two independent shared indicators** — a number *and* a payment handle,
      say. Coincidence has to work much harder for two.
    * **one shared indicator plus the same script** — the crew reused its number
      and told both residents the same story.

    A crew working a neighbourhood clears this without trying. A bank's helpline
    quoted in four unrelated scams does not, and neither does one resident's
    report that muddles two different calls together.
    """
    shared = keys_a & keys_b
    if not shared:
        return False
    if len(shared) >= 2:
        return True

    fa = (a.get("script_fingerprint") or "").strip().lower()
    fb = (b.get("script_fingerprint") or "").strip().lower()
    if fa in SENTINEL_FINGERPRINTS or fb in SENTINEL_FINGERPRINTS:
        # One shared indicator and no usable script on at least one side is the
        # weakest evidence there is. Two reports nobody could classify are not
        # thereby the same crew.
        return False
    return _fingerprints_match(fa, fb)


def _pack_endpoints_present(by_indicator: dict[str, list[int]]) -> set[str]:
    """Indicator keys that match an endpoint the jurisdiction pack publishes.

    A report that repeats the official helpline or portal is not thereby linked
    to every other report that does. This is the precise case; the corroboration
    rule above is the general one.
    """
    values = _pack_endpoints()
    if not values:
        return set()
    return {key for key in by_indicator if key.split(":", 1)[-1] in values}


def _pack_endpoints() -> set[str]:
    """Normalised identifiers the jurisdiction pack publishes as legitimate."""
    try:
        from .config import active_pack

        pack = active_pack()
    except Exception:  # noqa: BLE001 — no pack configured is not an error here
        return set()

    values: set[str] = set()
    reporting = pack.reporting or {}
    for name in ("helpline", "portal_url", "secondary_portal_url", "local_helpline"):
        raw = str(reporting.get(name, "") or "")
        if not raw:
            continue
        digits = re.sub(r"\D", "", raw)
        if digits:
            values.add(digits[-10:] if len(digits) >= 10 else digits)
        values.add(raw.strip().lower())
    return {v for v in values if v}


def build_clusters(records: Iterable[dict[str, Any]]) -> list[Cluster]:
    """Union-find over hard indicators, then a fingerprint pass.

    Two records join a cluster if they share a hard indicator (a callback number,
    a UPI id, a wallet, a URL), or if their script fingerprints are near-identical
    AND they were filed inside the correlation window by different reporters.
    """
    recs = list(records)
    parent: dict[int, int] = {i: i for i in range(len(recs))}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Pass 1 — hard indicators, with a corroboration requirement on every link.
    #
    # One shared indicator is not enough on its own, and that is the correction
    # the challenge corpus forced. Several unrelated scams all tell the victim
    # "ring your bank on the number on your card"; every one of those reports
    # then carries the same real helpline, and a single-indicator rule fuses them
    # into one fictitious campaign — broadcast to frightened people on the
    # strength of a bank's customer-service line. The same shape appears when one
    # resident muddles two different calls together into a single report and
    # bridges two genuine crews through it.
    #
    # So a link needs either two independent shared indicators, or one shared
    # indicator plus the same script. A crew working a neighbourhood satisfies
    # that easily — it reuses its number *and* its payment handle *and* its
    # script. Shared infrastructure does not.
    by_indicator: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(recs):
        for k in r.get("indicator_keys", []):
            by_indicator[str(k).lower()].append(i)

    dropped = _pack_endpoints_present(by_indicator)
    keysets = [
        {str(k).lower() for k in r.get("indicator_keys", [])} - dropped
        for r in recs
    ]

    # Only pairs that share at least one indicator are worth examining, so this
    # walks the index rather than every pair in the corpus.
    candidates: set[tuple[int, int]] = set()
    for key, idxs in by_indicator.items():
        if key in dropped or len(idxs) > _MAX_REPORTS_PER_INDICATOR:
            continue
        for a_pos in range(len(idxs)):
            for b_pos in range(a_pos + 1, len(idxs)):
                candidates.add((idxs[a_pos], idxs[b_pos]))

    for i, j in candidates:
        if _link_is_corroborated(recs[i], recs[j], keysets[i], keysets[j]):
            union(i, j)

    # Pass 2 — fingerprint similarity, deliberately fenced.
    #
    # This pass exists for the case where one crew rotates its numbers between
    # victims, so no hard indicator is shared. It is dangerous: fingerprint
    # matching is transitive, and an unfenced version collapses an entire corpus
    # into one "campaign", which is worse than useless — it is a false alarm
    # broadcast to a list of frightened people. So a fingerprint-only link
    # requires all of: a real (non-sentinel) fingerprint, different reporters,
    # the same area, a tight window, and neither report already anchored to a
    # hard-indicator cluster.
    anchored = {i for i, r in enumerate(recs) if r.get("indicator_keys")}
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            if find(i) == find(j):
                continue
            if i in anchored and j in anchored:
                continue
            ri, rj = recs[i], recs[j]
            fi = (ri.get("script_fingerprint") or "").strip().lower()
            fj = (rj.get("script_fingerprint") or "").strip().lower()
            if fi in SENTINEL_FINGERPRINTS or fj in SENTINEL_FINGERPRINTS:
                continue
            if ri.get("reporter_pseudonym") == rj.get("reporter_pseudonym"):
                continue
            if ri.get("reporter_area") != rj.get("reporter_area"):
                continue
            if abs((_ts(ri) - _ts(rj)).days) > _FP_LINK_MAX_DAYS:
                continue
            if not _fingerprints_match(fi, fj):
                continue
            # Do not let fingerprint-only links grow an unbounded cluster.
            size = sum(1 for k in range(len(recs)) if find(k) in {find(i), find(j)})
            if size > _FP_LINK_MAX_CLUSTER:
                continue
            union(i, j)

    grouped: dict[int, Cluster] = defaultdict(Cluster)
    for i, r in enumerate(recs):
        c = grouped[find(i)]
        c.members.append(r)
        fp = (r.get("script_fingerprint") or "").strip().lower()
        if fp not in SENTINEL_FINGERPRINTS:
            c.fingerprints.add(fp)

    for c in grouped.values():
        counts: dict[str, int] = defaultdict(int)
        for m in c.members:
            for k in {str(k).lower() for k in m.get("indicator_keys", [])}:
                counts[k] += 1
        c.shared_indicators = {k for k, n in counts.items() if n >= 2}

    return sorted(grouped.values(), key=lambda c: (-len(c.members), c.campaign_id))


def cluster_for_report(report_id: str, records: Iterable[dict[str, Any]]) -> Cluster | None:
    for c in build_clusters(records):
        if report_id in c.report_ids:
            return c
    return None


def escalated_by(report_id: str, records: Iterable[dict[str, Any]]) -> bool:
    """Did *this* report tip its cluster over the campaign threshold?

    Answered by counterfactual, not by arithmetic: rebuild the clustering with
    this report removed and ask whether the group it belongs to was already a
    campaign. If it was not, and now it is, this report is what fired it.

    The obvious shortcut — ``len(members) == campaign_min_reports`` — is wrong in
    the case that matters most. A report carrying an indicator shared by two
    previously separate pairs merges them and jumps the cluster from 2 to 5, and
    the shortcut reports that as "not new" precisely when a coordinator most
    needs to be told. It also silently mislabels any run where reports arrive out
    of order, which is every real inbox.
    """
    recs = list(records)
    after = cluster_for_report(report_id, recs)
    if after is None or not meets_threshold(after):
        return False

    without = [r for r in recs if r.get("report_id") != report_id]
    members_before = {rid for rid in after.report_ids if rid != report_id}
    for c in build_clusters(without):
        if set(c.report_ids) & members_before and meets_threshold(c):
            return False
    return True


def meets_threshold(c: Cluster) -> bool:
    """A cluster is only a campaign if it clears volume, breadth AND evidence.

    The evidence clause is the important one. Three reports from three residents
    that share nothing but a common genre of scam is not a local crew, and
    broadcasting it would spend the coalition's credibility on noise.
    """
    s = settings()
    if len(c.members) < s.campaign_min_reports:
        return False
    if c.distinct_reporters < s.campaign_min_distinct_reporters:
        return False
    has_hard_evidence = bool(c.shared_indicators)
    has_tight_script = len(c.fingerprints) == 1 and len(c.areas) <= 2
    return has_hard_evidence or has_tight_script


@tool
def find_candidate_cluster(report_id: str) -> dict:
    """Group this report with prior community reports that may share a crew.

    Clustering is deterministic — it joins reports on shared hard indicators and
    on near-identical script fingerprints across different reporters. Your job is
    to judge whether the resulting group is a real campaign.

    Args:
        report_id: The id of the report currently being processed.

    Returns:
        A dict describing the candidate cluster, or one with `found: false`.
    """
    s = settings()
    recs = list(iter_records(s.community_id, s.campaign_window_days))
    c = cluster_for_report(report_id, recs)
    if c is None or len(c.members) < 2:
        return {"found": False, "detail": "no other report in the window shares an indicator or script"}
    return {
        "found": True,
        "campaign_id": c.campaign_id,
        "member_report_ids": c.report_ids,
        "shared_indicators": sorted(c.shared_indicators),
        "script_fingerprints": sorted(f for f in c.fingerprints if f),
        "distinct_reporters": c.distinct_reporters,
        "areas_affected": c.areas,
        "first_seen": c.first_seen.isoformat(),
        "last_seen": c.last_seen.isoformat(),
        "meets_threshold": meets_threshold(c),
        "computed_confidence": c.confidence(),
        "thresholds": {
            "min_reports": s.campaign_min_reports,
            "min_distinct_reporters": s.campaign_min_distinct_reporters,
            "window_days": s.campaign_window_days,
        },
    }
