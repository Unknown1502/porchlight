"""Command line entry points.

    python -m porchlight.cli replay --dir corpus/seed      # the demo
    python -m porchlight.cli one --file report.json        # single report
    python -m porchlight.cli reset                         # clear community memory
    python -m porchlight.cli setup-memory                  # create AgentCore Memory
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import settings
from .models import Report
from .pipeline import process_report
from .tools.store import get_store

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
BAND = {"black": "\033[97;41m", "red": "\033[91m", "amber": "\033[93m", "green": "\033[92m"}


def _load_reports(path: Path) -> list[Report]:
    raw = json.loads((path / "reports.json").read_text(encoding="utf-8"))
    return [Report(**{k: v for k, v in r.items() if not k.startswith("_")}) for r in raw]


def cmd_replay(args: argparse.Namespace) -> int:
    reports = _load_reports(Path(args.dir))
    if args.reset:
        get_store().clear(settings().community_id)
    print(f"{BOLD}Porchlight{RESET} — replaying {len(reports)} reports "
          f"(offline={settings().offline}, pack={settings().pack_name})\n")
    fired = 0
    for r in reports:
        case = process_report(r)
        band = case.stage.urgency.value if case.stage else "?"
        colour = BAND.get(band, "")
        camp = ""
        if case.campaign and case.campaign.is_campaign and case.campaign.match:
            m = case.campaign.match
            camp = (f"  {BOLD}\033[95m>>> CAMPAIGN {m.campaign_id}{RESET} "
                    f"{len(m.member_report_ids)} reports / {m.distinct_reporters} residents "
                    f"/ conf {m.confidence}")
            if case.campaign.newly_escalated:
                fired += 1
                camp += f"  {BOLD}[NEWLY ESCALATED]{RESET}"
        inj = f" {DIM}[injection flagged]{RESET}" if case.intake and case.intake.contains_injection_attempt else ""
        print(f"{colour}{band.upper():<6}{RESET} {r.report_id}  "
              f"{(case.intake.script_fingerprint if case.intake else '?'):<38} "
              f"{r.reporter_area:<8}{inj}")
        if camp:
            print(camp)
            if case.response:
                print(f"       {DIM}decision:{RESET} {case.response.decision_for_human}")
        if args.speed:
            time.sleep(1.0 / args.speed)
    print(f"\n{BOLD}{fired}{RESET} campaign escalation(s) fired.")
    return 0


def cmd_one(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    case = process_report(Report(**{k: v for k, v in data.items() if not k.startswith("_")}))
    print(json.dumps(case.model_dump(mode="json"), indent=2, default=str))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    get_store().clear(settings().community_id)
    print(f"cleared community store for {settings().community_id}")
    return 0


def cmd_setup_memory(args: argparse.Namespace) -> int:
    from .memory import create_memory_resource

    res = create_memory_resource()
    mem_id = res.get("id") or res.get("memoryId") or res
    print(f"AgentCore Memory created.\n\n  AGENTCORE_MEMORY_ID={mem_id}\n\nAdd that to your .env.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="porchlight")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("replay", help="run the corpus through the pipeline")
    p.add_argument("--dir", default="corpus/seed")
    p.add_argument("--speed", type=float, default=0, help="reports per second; 0 = as fast as possible")
    p.add_argument("--reset", action="store_true", default=True)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("one", help="process a single report json file")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_one)

    p = sub.add_parser("reset", help="clear the community store")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("setup-memory", help="create the AgentCore Memory resource")
    p.set_defaults(func=cmd_setup_memory)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
