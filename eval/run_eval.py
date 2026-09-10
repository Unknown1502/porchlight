#!/usr/bin/env python3
"""Evaluation harness — the numbers a judge can reproduce from the repo.

Everything reported here is measured against ground truth emitted by the corpus
generator in the same run. Nothing here is an estimate, and where a number would
have to be an estimate it is not reported at all.

What is measured:

1. **Campaign attribution** — precision, recall and F1 for each planted campaign,
   plus a corpus-wide count of false-positive and false-negative links. The
   second part matters more than the first: scoring only the cluster you were
   hoping to find rewards a system that clusters everything.
2. **Injection detection** — scored twice, on two disjoint sets. The
   in-vocabulary set shares its phrasing with the detector's signature list, so
   it is a regression check. The held-out set does not, and is the only one that
   says anything about catching an adversary who is trying.
3. **Pipeline latency** — wall clock per report, reported as a raw number in the
   mode it was run in. There is no speed-up ratio here on purpose; see the note
   on ``manual baseline`` below.

On the missing manual baseline
------------------------------
An earlier version of this file carried ``MANUAL_BASELINE_SECONDS = 18 * 60``
and divided by it to produce a "speedup". The comment said it had been measured
by timing a coordinator; no timing data, procedure or record exists anywhere in
this repository, and the divisor was the offline stub's own latency, which
yielded a ratio in the hundreds of thousands. Both halves were unsupported, so
both are gone. ``docs/manual-baseline.md`` sets out a procedure for measuring one
honestly; until someone runs it, this harness reports no comparison.

Run:  python eval/run_eval.py --corpus corpus/seed --labels eval/labels.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from porchlight.config import settings  # noqa: E402
from porchlight.models import Report  # noqa: E402
from porchlight.pipeline import process_report  # noqa: E402
from porchlight.tools.store import get_store  # noqa: E402


def prf(predicted: set[str], truth: set[str]) -> tuple[float, float, float]:
    if not predicted and not truth:
        return 1.0, 1.0, 1.0
    tp = len(predicted & truth)
    p = tp / len(predicted) if predicted else 0.0
    r = tp / len(truth) if truth else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def pairwise_link_scores(clusters: dict[str, set[str]], crews: dict[str, str]) -> dict:
    """Score every *link* the system asserted, against crew ground truth.

    A cluster is a claim that its members share a crew. Turning each cluster into
    the set of pairs it asserts, and comparing against the pairs that really do
    share a crew, gives a false-positive count that per-campaign F1 cannot: it
    counts the wrong links inside clusters nobody was scoring, and the real links
    that were never made at all.

    Reports whose ground-truth crew is ``none`` (the benign false alarms) are
    excluded — they belong to no crew, so "should these two be linked" has no
    meaningful answer for them beyond "no", which the predicted side already
    handles.
    """
    scored = {rid: crew for rid, crew in crews.items() if crew and crew != "none"}

    predicted_pairs: set[tuple[str, str]] = set()
    for members in clusters.values():
        real = sorted(m for m in members if m in scored)
        predicted_pairs |= {tuple(sorted(pair)) for pair in combinations(real, 2)}

    by_crew: dict[str, list[str]] = {}
    for rid, crew in scored.items():
        by_crew.setdefault(crew, []).append(rid)
    truth_pairs: set[tuple[str, str]] = set()
    for members in by_crew.values():
        truth_pairs |= {tuple(sorted(pair)) for pair in combinations(sorted(members), 2)}

    tp = predicted_pairs & truth_pairs
    fp = predicted_pairs - truth_pairs
    fn = truth_pairs - predicted_pairs
    p, r, f = prf(set(map(str, predicted_pairs)), set(map(str, truth_pairs)))
    return {
        "note": "one 'link' = one asserted pair of reports sharing a crew",
        "precision": p, "recall": r, "f1": f,
        "true_positive_links": len(tp),
        "false_positive_links": len(fp),
        "false_negative_links": len(fn),
        "false_positive_examples": [list(x) for x in sorted(fp)[:5]],
    }



def challenge_scores(clusters: dict[str, set[str]], labels: dict) -> dict:
    """Score the cases most likely to break correlation, one at a time.

    An aggregate hides where a system actually fails. These are named so a
    regression shows up as "shared legitimate infrastructure broke" rather than
    as a decimal moving.
    """
    crews = labels.get("crews", {})
    challenges = labels.get("challenges", {})
    if not challenges:
        return {"note": "corpus carries no challenge labels; regenerate it"}

    wrong_links: dict[str, list] = {}
    for members in clusters.values():
        real = sorted(m for m in members if crews.get(m, "none") != "none")
        for a_i in range(len(real)):
            for b_i in range(a_i + 1, len(real)):
                a, b = real[a_i], real[b_i]
                if crews.get(a) == crews.get(b):
                    continue
                for rid in (a, b):
                    tag = challenges.get(rid)
                    if tag:
                        wrong_links.setdefault(tag, []).append([a, b])

    out = {}
    for tag in sorted(set(challenges.values())):
        members = [r for r, t in challenges.items() if t == tag]
        bad = wrong_links.get(tag, [])
        out[tag] = {
            "reports": len(members),
            "wrong_links_caused": len(bad),
            "passed": not bad,
            "examples": bad[:3],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="corpus/seed")
    ap.add_argument("--labels", default="eval/labels.json")
    ap.add_argument("--out", default="eval/out/results.json")
    args = ap.parse_args()

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    raw = json.loads((Path(args.corpus) / "reports.json").read_text(encoding="utf-8"))
    reports = [Report(**{k: v for k, v in r.items() if not k.startswith("_")}) for r in raw]

    get_store().clear(settings().community_id)

    latencies: list[float] = []
    predicted_campaign: dict[str, set[str]] = {}
    injection_flagged: set[str] = set()
    denials = 0
    complaint_drafted = 0

    for r in reports:
        t0 = time.perf_counter()
        case = process_report(r)
        latencies.append(time.perf_counter() - t0)

        if case.intake and case.intake.contains_injection_attempt:
            injection_flagged.add(r.report_id)
        denials += sum(1 for e in case.policy_events if e["effect"] == "forbid")
        if case.response and any(d.kind.value == "official_complaint" for d in case.response.drafts):
            complaint_drafted += 1
        if case.campaign and case.campaign.is_campaign and case.campaign.match:
            cid = case.campaign.match.campaign_id
            predicted_campaign.setdefault(cid, set()).update(case.campaign.match.member_report_ids)

    truth_campaigns = {k: set(v) for k, v in labels["campaigns"].items()}

    # Score each ground-truth campaign against its best-matching predicted cluster.
    campaign_scores = {}
    for name, truth_set in truth_campaigns.items():
        best = ("none", 0.0, 0.0, 0.0)
        for cid, pred in predicted_campaign.items():
            p, r_, f = prf(pred, truth_set)
            if f > best[3]:
                best = (cid, p, r_, f)
        campaign_scores[name] = {
            "matched_cluster": best[0], "precision": best[1],
            "recall": best[2], "f1": best[3], "truth_size": len(truth_set),
        }

    links = pairwise_link_scores(predicted_campaign, labels.get("crews", {}))
    challenges = challenge_scores(predicted_campaign, labels)

    in_vocab = set(labels.get("injection_report_ids", []))
    heldout = set(labels.get("injection_heldout_report_ids", []))
    iv_p, iv_r, iv_f = prf(injection_flagged & (in_vocab | heldout), in_vocab | heldout)
    ho_flagged = injection_flagged & heldout
    ho_recall = round(len(ho_flagged) / len(heldout), 3) if heldout else None

    median_latency = statistics.median(latencies)
    mode = "offline-deterministic" if settings().offline else "bedrock-agents"

    results = {
        "mode": mode,
        "reports_processed": len(reports),
        "campaign_attribution": campaign_scores,
        "predicted_cluster_count": len(predicted_campaign),
        "link_level": links,
        "challenge_set": challenges,
        "injection_detection": {
            "in_vocabulary": {
                "caveat": "payloads share phrasing with the detector's signature list; "
                          "this is a regression check, not a capability measurement",
                "planted": len(in_vocab),
                "flagged": len(injection_flagged & in_vocab),
                "recall": round(len(injection_flagged & in_vocab) / len(in_vocab), 3)
                if in_vocab else None,
            },
            "held_out": {
                "caveat": "paraphrase, homoglyph, spacing, encoding and non-English evasions "
                          "the detector was never tuned on; this is the honest number",
                "planted": len(heldout),
                "flagged": len(ho_flagged),
                "recall": ho_recall,
                "missed": sorted(heldout - ho_flagged)[:10],
            },
            "combined_precision": iv_p,
            "combined_recall": iv_r,
            "combined_f1": iv_f,
        },
        "policy_denials": denials,
        "complaints_drafted": complaint_drafted,
        "latency": {
            "note": f"per-report wall clock in {mode} mode. No manual-baseline comparison "
                    f"is reported; see docs/manual-baseline.md.",
            "median_seconds": round(median_latency, 3),
            "p95_seconds": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 3),
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(json.dumps(results, indent=2))
    print(f"\nwrote {out}")

    # Gates. Deliberately set on the numbers that mean something: attribution F1,
    # and link-level precision — a system that starts merging unrelated crews
    # fails here before it fails on camera. There is no gate on held-out
    # injection recall, because a signature list is not the control and pinning a
    # floor to it would invite tuning the corpus instead of fixing the system.
    worst_f1 = min((v["f1"] for v in campaign_scores.values()), default=0.0)
    if worst_f1 < 0.80:
        print(f"\nFAIL: campaign attribution F1 {worst_f1} is below the 0.80 gate.")
        return 1
    broken = [name for name, r in challenges.items()
              if isinstance(r, dict) and r.get("passed") is False]
    if broken:
        print(f"\nFAIL: challenge case(s) regressed: {', '.join(broken)}")
        return 1
    if links["precision"] < 0.90:
        print(f"\nFAIL: link precision {links['precision']} is below the 0.90 gate — "
              f"{links['false_positive_links']} wrong links asserted.")
        return 1
    print("\nPASS: attribution and link-precision gates met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
