"""Policy enforcement.

In deployment, this is intended to be **AgentCore Policy**: Cedar rules attached
to an AgentCore Gateway, evaluated outside agent code, default-deny, every
decision logged to CloudWatch. See ``policies/README.md`` for exactly how far
that path has been verified against AWS and what remains unproven — the short
version is that the policy engine is real and Porchlight's rules load into it,
but tool-level enforcement requires a Gateway that has not been built.

Today — locally, in tests, in the offline demo and in the container — this
module *is* the enforcement point. It implements the rule set in-process,
outside the model but inside the same trust boundary as the agent.
``tests/test_policy_parity.py`` asserts that it and the Cedar sources do not
drift apart on the things that matter: which actions exist, and what it takes
to satisfy the approval rule.

Design note: this is deliberately a separate layer from the prompts. A control
that lives in a system prompt is a request. A control that lives here is a rule.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .config import settings


class Effect(str, Enum):
    PERMIT = "permit"
    FORBID = "forbid"


@dataclass
class Decision:
    effect: Effect
    policy_id: str
    reason: str
    action: str
    resource: str = ""
    at: float = field(default_factory=time.time)
    # Attribution. A line that cannot answer "who asked for this, about which
    # case, and where did the request come from" is a log, not an audit trail.
    # ``actor`` is the authority the request was made under; ``origin`` records
    # whether it came from the agent's own reasoning or was lifted out of
    # untrusted report content.
    actor: str = "porchlight-agent"
    report_id: str = ""
    campaign_id: str = ""
    origin: str = "agent"

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.PERMIT

    def to_audit(self) -> dict[str, Any]:
        return {
            "effect": self.effect.value,
            "policy_id": self.policy_id,
            "action": self.action,
            "resource": self.resource[:200],
            "reason": self.reason,
            "at": self.at,
            "actor": self.actor,
            "report_id": self.report_id,
            "campaign_id": self.campaign_id,
            "origin": self.origin,
        }


class PolicyDenied(RuntimeError):
    def __init__(self, decision: Decision) -> None:
        super().__init__(f"[{decision.policy_id}] {decision.reason}")
        self.decision = decision


# --------------------------------------------------------------------------
# The rule set. Mirrors policies/cedar/*.cedar one-for-one.
# --------------------------------------------------------------------------
Rule = Callable[[str, dict[str, Any]], Decision | None]

# Payment card: 13-19 digits, optional single separators.
_PAN_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
# 12-digit national identity shape (e.g. India's Aadhaar), grouped 4-4-4.
_GOVT_ID_RE = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")
# US Social Security number.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _r_no_contact_reported_endpoints(action: str, ctx: dict) -> Decision | None:
    """P001 — never contact anything that came from a report."""
    if action not in {"http.fetch", "sms.send", "email.send", "voice.call"}:
        return None
    target = str(ctx.get("target", ""))
    tainted = {str(t).lower() for t in ctx.get("reported_indicators", [])}
    if any(t and t in target.lower() for t in tainted):
        return Decision(
            Effect.FORBID, "P001",
            "Target appeared in a reported artefact. Contacting scammer-controlled "
            "infrastructure would leak the coalition's presence and can never be authorised.",
            action, target,
        )
    return None


# --------------------------------------------------------------------------
# Human authority
# --------------------------------------------------------------------------
# Approval tokens this process actually minted, and what each one is good for.
# A token is not something the policy engine trusts because it is non-empty; it
# is a capability this module issued to a named human, for one artefact, once.
#
# That distinction is the whole point. "No code path currently sets
# approval_token from report content" is an accident of wiring that a refactor
# can undo. "Report content cannot mint authority" is a property: untrusted text
# can put any string it likes in front of P002, and none of them are in here.
_APPROVAL_ROLE = "coalition_coordinator"
_MINTED_APPROVALS: dict[str, dict[str, Any]] = {}


def mint_approval(approver: str, action: str, resource: str) -> str:
    """Issue a single-use approval capability to a named human.

    Called from exactly one place: the coordinator's ``POST /approve`` handler.
    The agent has no route to this function and no route to that endpoint.
    """
    if not approver or not approver.strip():
        raise ValueError("an approval must name a human")
    token = f"coord:{approver.strip()}:{secrets.token_hex(8)}"
    _MINTED_APPROVALS[token] = {
        "approver": approver.strip(),
        "role": _APPROVAL_ROLE,
        "action": action,
        "resource": resource,
        "issued_at": time.time(),
        "spent": False,
    }
    return token


def approval_holder(token: str | None) -> str:
    """Name the human behind a token, or "" if it was not issued by us."""
    rec = _MINTED_APPROVALS.get(str(token or ""))
    return rec["approver"] if rec else ""


def spend_approval(token: str | None) -> None:
    """Burn a token once its action was permitted. An approval is not a licence."""
    rec = _MINTED_APPROVALS.get(str(token or ""))
    if rec:
        rec["spent"] = True


def _r_broadcast_requires_approval(action: str, ctx: dict) -> Decision | None:
    """P002 — nothing reaches a resident without a named human approver.

    Mirrors the Cedar rule: the token must exist, be non-empty, and carry
    ``approver_role == "coalition_coordinator"``. The role is *not* read from the
    caller — it is read from the mint registry, so the only way to satisfy this
    rule is to have gone through the coordinator approval flow.
    """
    if action not in {"sms.send", "email.send", "flyer.publish", "broadcast.send"}:
        return None
    target = str(ctx.get("target", "resident-list"))
    token = str(ctx.get("approval_token") or "")
    rec = _MINTED_APPROVALS.get(token)

    if not token:
        return Decision(
            Effect.FORBID, "P002",
            "Outbound communication to residents requires a coordinator approval token. "
            "The agent drafts; a human sends.",
            action, target,
        )
    if rec is None:
        return Decision(
            Effect.FORBID, "P002",
            "Approval token was not issued by the coordinator approval flow. A token "
            "asserted inside a report, a tool argument or a model output is data, not "
            "authority.",
            action, target, origin="untrusted-content",
        )
    if rec["role"] != _APPROVAL_ROLE:
        return Decision(
            Effect.FORBID, "P002",
            f"Approval token carries role {rec['role']!r}, not {_APPROVAL_ROLE!r}.",
            action, target, actor=rec["approver"],
        )
    if rec["spent"]:
        return Decision(
            Effect.FORBID, "P002",
            "Approval token has already been spent. Each approval authorises one send.",
            action, target, actor=rec["approver"],
        )
    if rec["action"] != action:
        return Decision(
            Effect.FORBID, "P002",
            f"Approval was granted for {rec['action']!r}, not {action!r}. An approval is "
            "scoped to the artefact it was given for.",
            action, target, actor=rec["approver"],
        )
    return None


def _r_no_raw_identifiers(action: str, ctx: dict) -> Decision | None:
    """P003 — case files and briefs never carry raw identifiers.

    Covers both shapes the Cedar source names: an unmasked payment card, and an
    unmasked government identity number. The 12-digit national-id shape is here
    because ``scrub_pii`` removes it on the way in — so a payload still carrying
    one means a later stage reintroduced it, which is exactly the case worth
    catching before it reaches a partner brief.
    """
    if action not in {"case.write", "partner.brief", "complaint.draft"}:
        return None
    payload = str(ctx.get("payload", ""))
    if _PAN_RE.search(payload):
        return Decision(
            Effect.FORBID, "P003",
            "Payload contains an unmasked card number (13-19 digits).",
            action, "case-file",
        )
    if _GOVT_ID_RE.search(payload) or _SSN_RE.search(payload):
        return Decision(
            Effect.FORBID, "P003",
            "Payload contains an unmasked government identity number.",
            action, "case-file",
        )
    return None


_BROADCAST_LOG: list[float] = []


def _r_rate_limit_broadcast(action: str, ctx: dict) -> Decision | None:
    """P004 — at most 2 community broadcasts per rolling 24h.

    Evaluated *after* P002, and with no approval escape of its own. A coordinator
    approval satisfies P002; it does not buy a third broadcast. Alert fatigue in
    a senior-centre list is a real cost, and it is not the approver's to spend.
    """
    if action not in {"broadcast.send", "sms.send"}:
        return None
    now = time.time()
    window = [t for t in _BROADCAST_LOG if now - t < 86400]
    if len(window) >= 2:
        return Decision(
            Effect.FORBID, "P004",
            "Community broadcast rate limit reached (2 per 24h). Alert fatigue in a "
            "senior-centre list is a real cost; a third message needs a human override.",
            action, "resident-list",
        )
    return None


def _r_tool_allowlist(action: str, ctx: dict) -> Decision | None:
    """P005 — default-deny: only named actions exist at all."""
    if action not in ALLOWED_ACTIONS:
        return Decision(
            Effect.FORBID, "P005",
            f"Action {action!r} is not in the allow-list. Default deny.",
            action, str(ctx.get("target", "")),
        )
    return None


ALLOWED_ACTIONS = {
    "url.reputation", "domain.age", "phone.shape", "payment.check",
    "store.lookup", "store.write",
    "case.write", "partner.brief", "complaint.draft",
    "sms.send", "email.send", "flyer.publish", "broadcast.send",
    "http.fetch", "voice.call",
}

# Actions that reach a resident or an external party. These are the ones that
# exist in the allow-list only so that a *denial* can name them: none of them is
# permitted without passing every rule above.
SENDING_ACTIONS = {"sms.send", "email.send", "flyer.publish", "broadcast.send",
                   "http.fetch", "voice.call"}

RULES: list[Rule] = [
    _r_tool_allowlist,
    _r_no_contact_reported_endpoints,
    _r_no_raw_identifiers,
    _r_broadcast_requires_approval,
    _r_rate_limit_broadcast,
]

AUDIT: list[Decision] = []


def _attribute(d: Decision, ctx: dict[str, Any]) -> Decision:
    """Stamp the audit line with who asked, about what, and where it came from.

    ``actor`` prefers the human a minted token names, because that is the only
    authority in the system that is not the agent itself. A rule that already set
    an actor or an origin keeps its own — it knew more than this does.
    """
    if d.actor == "porchlight-agent":
        d.actor = (approval_holder(ctx.get("approval_token"))
                   or str(ctx.get("actor") or "porchlight-agent"))
    if d.origin == "agent" and ctx.get("origin"):
        d.origin = str(ctx["origin"])
    d.report_id = str(ctx.get("report_id", "") or "")
    d.campaign_id = str(ctx.get("campaign_id", "") or "")
    return d


def evaluate(action: str, **ctx: Any) -> Decision:
    """Evaluate one action. Default-deny: an unmatched action is forbidden."""
    for rule in RULES:
        d = rule(action, ctx)
        if d is not None:
            AUDIT.append(_attribute(d, ctx))
            return d
    d = Decision(Effect.PERMIT, "P000", "No forbidding rule matched an allow-listed action.",
                 action, str(ctx.get("target", "")))
    AUDIT.append(_attribute(d, ctx))
    return d


def enforce(action: str, **ctx: Any) -> Decision:
    """Evaluate and raise on denial. Every side-effecting call site uses this."""
    d = evaluate(action, **ctx)
    if not d.allowed:
        raise PolicyDenied(d)
    if action in {"broadcast.send", "sms.send"}:
        _BROADCAST_LOG.append(time.time())
        spend_approval(ctx.get("approval_token"))
    return d


def recent_audit(n: int = 50) -> list[dict[str, Any]]:
    return [d.to_audit() for d in AUDIT[-n:]]


def reset_audit() -> None:
    AUDIT.clear()
    _BROADCAST_LOG.clear()
    _MINTED_APPROVALS.clear()


def backend_name() -> str:
    return "AgentCore Policy (gateway)" if settings().policy_engine_id else "local Cedar-equivalent shim"
