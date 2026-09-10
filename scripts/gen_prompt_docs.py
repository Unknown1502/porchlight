#!/usr/bin/env python3
"""Regenerate docs/prompts.md from the prompt modules.

The prompts are code. This keeps the document that judges read identical to the
text the agents actually run, so the two can never drift. Run: `make docs`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from porchlight import prompts as P  # noqa: E402
from porchlight.prompts import _shared  # noqa: E402

HEADER = (ROOT / "docs" / "_prompts_header.md").read_text(encoding="utf-8")

SECTIONS = [
    ("Intake", "Reads the raw artefact. Zero tools, because it is the node an injection lands on first.", P.INTAKE_PROMPT),
    ("Corroboration", "The only node that makes outbound calls. Its `tools=` list is the capability surface.", P.CORROBORATION_PROMPT),
    ("Script matcher", "Swarm specialist. Locates the resident in the script's own sequence.", P.SCRIPT_MATCHER_PROMPT),
    ("Money rail", "Swarm specialist. Judges reversibility, which is what sets the clock.", P.MONEY_RAIL_PROMPT),
    ("Isolation", "Swarm specialist. Reads coercion - decides *who* should make contact.", P.ISOLATION_PROMPT),
    ("Campaign", "Judges a deterministically-built cluster. Strict, because a false campaign is a false alarm broadcast to frightened people.", P.CAMPAIGN_PROMPT),
    ("Response", "Drafts only. No send tool exists for it to reach.", P.RESPONSE_PROMPT),
]

FOOTER = (ROOT / "docs" / "_prompts_footer.md").read_text(encoding="utf-8")


def main() -> int:
    body = (HEADER
            .replace("__ADV__", _shared.ADVERSARIAL_INPUT_RULE)
            .replace("__PRIV__", _shared.PRIVACY_RULE)
            .replace("__MISSION__", _shared.MISSION)
            .replace("__OUT__", _shared.OUTPUT_RULE))
    for title, blurb, text in SECTIONS:
        body += f"\n## {title}\n\n{blurb}\n\n```text\n{text}\n```\n\n---\n"
    body += ("\n## Swarm task template\n\nThe user turn handed to the stage swarm. Note the trust "
             "labelling: each block\nsays what it is and how far to trust it, and the artefact comes "
             "last so untrusted\nmaterial never precedes the instructions that frame it.\n\n```text\n"
             + P.STAGE_TASK_TEMPLATE + "\n```\n\n---\n")
    body += FOOTER
    out = ROOT / "docs" / "prompts.md"
    out.write_text(body, encoding="utf-8")
    print(f"wrote {out} ({len(body.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
