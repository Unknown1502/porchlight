#!/usr/bin/env python3
"""Run the evaluation across several corpus seeds and report the spread.

One seed is an anecdote. A clustering rule can be tuned, accidentally, until it
happens to fit one generated corpus — and a single headline number gives no way
to tell that from a rule that generalises.

So this regenerates the corpus under N seeds, scores each, and prints the
distribution. The number worth quoting is the **worst** case, not the mean:
a coordinator is not comforted by the average day.

    python eval/run_seeds.py --seeds 5
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run_one(seed: int, workdir: Path) -> dict:
    """Generate a corpus at this seed and evaluate it, without touching demo state."""
    out = workdir / f"seed-{seed}"
    out.mkdir(parents=True, exist_ok=True)
    labels = out / "labels.json"
    results = out / "results.json"

    gen = subprocess.run(
        [PY, str(ROOT / "corpus" / "generate.py"), "--out", str(out),
         "--count", "60", "--campaigns", "1", "--seed", str(seed)],
        cwd=ROOT, capture_output=True, text=True)
    if gen.returncode != 0:
        raise RuntimeError(f"corpus generation failed at seed {seed}: {gen.stderr[-400:]}")

    # generate.py writes eval/labels.json; move it beside this seed's corpus so
    # concurrent or repeated runs cannot read each other's ground truth.
    produced = ROOT / "eval" / "labels.json"
    labels.write_text(produced.read_text(encoding="utf-8"), encoding="utf-8")

    env_db = out / "seed.db"
    ev = subprocess.run(
        [PY, str(ROOT / "eval" / "run_eval.py"), "--corpus", str(out),
         "--labels", str(labels), "--out", str(results)],
        cwd=ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "PORCHLIGHT_DB": str(env_db)})
    if not results.exists():
        raise RuntimeError(f"eval produced nothing at seed {seed}: {ev.stdout[-400:]}")
    payload = json.loads(results.read_text(encoding="utf-8"))
    payload["_gate_passed"] = ev.returncode == 0
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--start", type=int, default=20260914)
    ap.add_argument("--out", default="eval/out/seeds.json")
    args = ap.parse_args()

    seeds = [args.start + i for i in range(args.seeds)]
    rows = []
    with tempfile.TemporaryDirectory(prefix="porchlight-seeds-") as tmp:
        workdir = Path(tmp)
        for seed in seeds:
            payload = run_one(seed, workdir)
            camp = next(iter(payload["campaign_attribution"].values()))
            link = payload["link_level"]
            challenge = payload.get("challenge_set", {})
            rows.append({
                "seed": seed,
                "attribution_f1": camp["f1"],
                "link_precision": link["precision"],
                "link_recall": link["recall"],
                "false_positive_links": link["false_positive_links"],
                "clusters": payload["predicted_cluster_count"],
                "heldout_injection_recall":
                    payload["injection_detection"]["held_out"]["recall"],
                "challenges_failed": sorted(
                    name for name, r in challenge.items()
                    if isinstance(r, dict) and r.get("passed") is False),
                "gate_passed": payload["_gate_passed"],
            })
            print(f"  seed {seed}: F1 {camp['f1']:.3f}  link P {link['precision']:.3f}  "
                  f"FP links {link['false_positive_links']}  "
                  f"held-out injection {payload['injection_detection']['held_out']['recall']}"
                  f"{'' if rows[-1]['gate_passed'] else '   GATE FAILED'}")

    def spread(field: str) -> dict:
        values = [r[field] for r in rows if r[field] is not None]
        return {"min": min(values), "median": statistics.median(values), "max": max(values)}

    summary = {
        "seeds": seeds,
        "runs": rows,
        "attribution_f1": spread("attribution_f1"),
        "link_precision": spread("link_precision"),
        "link_recall": spread("link_recall"),
        "heldout_injection_recall": spread("heldout_injection_recall"),
        "total_false_positive_links": sum(r["false_positive_links"] for r in rows),
        "all_gates_passed": all(r["gate_passed"] for r in rows),
        "challenges_failed_anywhere": sorted(
            {c for r in rows for c in r["challenges_failed"]}),
        "note": "Quote the minimum, not the mean. A coordinator is not comforted "
                "by the average day.",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nacross {len(seeds)} seeds")
    print(f"  attribution F1   min {summary['attribution_f1']['min']:.3f}  "
          f"median {summary['attribution_f1']['median']:.3f}")
    print(f"  link precision   min {summary['link_precision']['min']:.3f}  "
          f"median {summary['link_precision']['median']:.3f}")
    print(f"  link recall      min {summary['link_recall']['min']:.3f}  "
          f"median {summary['link_recall']['median']:.3f}")
    print(f"  false-pos links  {summary['total_false_positive_links']} in total")
    print(f"  held-out inject  min {summary['heldout_injection_recall']['min']}  "
          f"median {summary['heldout_injection_recall']['median']}")
    print(f"\nwrote {out}")

    if not summary["all_gates_passed"]:
        print("\nFAIL: at least one seed did not meet the gates.")
        return 1
    if summary["challenges_failed_anywhere"]:
        print(f"\nFAIL: challenge regressions: {summary['challenges_failed_anywhere']}")
        return 1
    print("\nPASS: every seed met the gates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
