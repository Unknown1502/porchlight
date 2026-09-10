"""System prompts for every Porchlight agent.

Two rules govern everything in this package:

1. **Untrusted content never enters a system prompt.** Reported artefacts are
   written by fraudsters. They are passed on the user turn, inside an explicit
   envelope (see :func:`envelope`), and every system prompt tells the model that
   whatever is inside that envelope is evidence to describe, never instructions
   to follow.

2. **Prompts do not carry authority.** No prompt in this package can authorise a
   side effect. Sending, publishing and contacting are decided by policy at the
   gateway (see ``policies/``), outside the model's reach. If an injection
   succeeds at the language level, it still cannot reach a tool.
"""
from .intake import INTAKE_PROMPT
from .corroboration import CORROBORATION_PROMPT
from .stage_swarm import (
    SCRIPT_MATCHER_PROMPT,
    MONEY_RAIL_PROMPT,
    ISOLATION_PROMPT,
    STAGE_TASK_TEMPLATE,
)
from .campaign import CAMPAIGN_PROMPT
from .response import RESPONSE_PROMPT

UNTRUSTED_OPEN = "<<<REPORTED_ARTEFACT id={rid}>>>"
UNTRUSTED_CLOSE = "<<<END_REPORTED_ARTEFACT>>>"


def envelope(report_id: str, content: str) -> str:
    """Wrap attacker-authored content so it is unambiguously data.

    The delimiters are deliberately unusual and the id is echoed in the opening
    tag, so a forged closing tag inside the content does not silently end the
    envelope without us noticing on the way out.
    """
    safe = content.replace(UNTRUSTED_CLOSE, "[REDACTED-FORGED-DELIMITER]")
    return f"{UNTRUSTED_OPEN.format(rid=report_id)}\n{safe}\n{UNTRUSTED_CLOSE}"


__all__ = [
    "INTAKE_PROMPT",
    "CORROBORATION_PROMPT",
    "SCRIPT_MATCHER_PROMPT",
    "MONEY_RAIL_PROMPT",
    "ISOLATION_PROMPT",
    "STAGE_TASK_TEMPLATE",
    "CAMPAIGN_PROMPT",
    "RESPONSE_PROMPT",
    "envelope",
]
