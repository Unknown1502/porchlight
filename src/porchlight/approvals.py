"""Decisions, approvals, and sandbox delivery.

The human decision boundary, expressed as code rather than as a promise.

The shape of the guarantee: an approval is a **capability**, minted by one
endpoint, bound to a named human, an action, an audience, and the exact bytes of
one message, with an expiry and a single-use redemption. It is not a boolean on a
draft. Everything that could otherwise weaken it is closed deliberately:

* **Report content cannot mint one.** Only ``approve()`` writes to the approvals
  table, and it is reachable only from the coordinator endpoint.
* **Editing invalidates.** The message digest is part of the capability, so
  changing a draft after approval means the approval no longer matches it. This
  is the difference between "approve" meaning *this message* and meaning *this
  kind of message*.
* **Redemption is atomic.** Two concurrent redemptions of one capability cannot
  both succeed — the database decides, not a check-then-act in Python.
* **Approval is not an override.** It satisfies P002 and nothing else. P001
  (never contact reported infrastructure), P003 (no raw identifiers) and P004
  (broadcast rate limit) are evaluated afterwards and can still refuse a fully
  approved send.

Delivery is a sandbox table. There is no real sender anywhere in this project,
which is a design decision rather than an unfinished integration.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta
from typing import Any, Optional

from . import db
from .config import settings
from .domain import (
    Approval,
    AuditEvent,
    Decision,
    DecisionState,
    OutboxMessage,
    message_digest,
    utcnow,
)
from .policy import Effect, evaluate

log = logging.getLogger(__name__)

APPROVAL_ROLE = "coalition_coordinator"

# Which sandbox channel each decision kind is delivered on.
CHANNELS = {
    "community_sms": "sms",
    "community_flyer": "noticeboard",
    "official_complaint": "portal",
    "partner_brief": "email",
    "victim_checklist": "handout",
}


class ApprovalError(RuntimeError):
    """Raised when a capability does not authorise what is being attempted."""

    def __init__(self, reason: str, policy_id: str = "P002") -> None:
        super().__init__(reason)
        self.reason = reason
        self.policy_id = policy_id


# --------------------------------------------------------------------------
# Creating decisions
# --------------------------------------------------------------------------
def raise_decision(*, community_id: str, kind: str, action: str, title: str, body: str,
                   audience: str, rationale: str, case_id: str = "", campaign_id: str = "",
                   evidence: Optional[dict[str, Any]] = None) -> Optional[Decision]:
    """Put one thing in front of the coordinator. Returns None if it is already there.

    The duplicate check is a database constraint rather than a lookup here, so
    two workers escalating the same campaign at the same moment still produce one
    inbox item. A coordinator who sees the same ask twice stops reading the inbox.
    """
    decision = Decision(
        decision_id=f"dec-{uuid.uuid4().hex[:10]}", community_id=community_id, kind=kind,
        action=action, title=title, body=body, audience=audience, rationale=rationale,
        case_id=case_id, campaign_id=campaign_id, evidence=evidence or {},
    )
    if not db.create_decision(decision):
        log.info("decision suppressed as duplicate: %s/%s", campaign_id, kind)
        return None
    db.append_audit(AuditEvent(
        action="decision.raise", effect="permit", policy_id="P000", origin="system",
        actor="porchlight-agent", case_id=case_id, campaign_id=campaign_id,
        decision_id=decision.decision_id, reason=rationale,
    ))
    return decision


def edit_decision(decision_id: str, body: str, title: Optional[str] = None) -> Decision:
    """Edit a draft.

    No approval is revoked here, and none needs to be: the capability is bound to
    the old digest, so it simply stops matching. Invalidation is a consequence of
    the data model rather than a cleanup step someone has to remember.
    """
    decision = db.get_decision(decision_id)
    if decision is None:
        raise KeyError(decision_id)
    db.update_decision_body(decision_id, body, title)
    db.append_audit(AuditEvent(
        action="decision.edit", effect="permit", policy_id="P000", origin="coordinator",
        decision_id=decision_id, case_id=decision.case_id,
        reason="draft edited; any outstanding approval no longer matches the message",
    ))
    updated = db.get_decision(decision_id)
    assert updated is not None
    return updated


def reject_decision(decision_id: str, approver: str, note: str = "") -> Decision:
    decision = db.get_decision(decision_id)
    if decision is None:
        raise KeyError(decision_id)
    db.resolve_decision(decision_id, DecisionState.REJECTED, by=approver)
    db.append_audit(AuditEvent(
        action="decision.reject", effect="permit", policy_id="P000", origin="coordinator",
        actor=approver, decision_id=decision_id, case_id=decision.case_id,
        campaign_id=decision.campaign_id, reason=note or "rejected by the coordinator",
    ))
    updated = db.get_decision(decision_id)
    assert updated is not None
    return updated


# --------------------------------------------------------------------------
# Minting and redeeming capabilities
# --------------------------------------------------------------------------
def mint(approver: str, action: str, audience: str, body: str, *,
         case_id: str = "", decision_id: str = "") -> Approval:
    """Issue a capability. The only path that writes an approval."""
    if not approver or not approver.strip():
        raise ApprovalError("an approval must name a human")
    ttl = timedelta(minutes=settings().approval_ttl_minutes)
    approval = Approval(
        token=f"cap-{secrets.token_urlsafe(24)}", approver=approver.strip(), role=APPROVAL_ROLE,
        action=action, audience=audience, message_hash=message_digest(body),
        case_id=case_id, decision_id=decision_id, expires_at=utcnow() + ttl,
    )
    db.put_approval(approval)
    return approval


def approve(decision_id: str, approver: str, note: str = "") -> dict[str, Any]:
    """Approve and deliver one decision, or explain precisely why not.

    Order matters and is deliberate:

    1. mint a capability for exactly this action, audience and message;
    2. check it still matches (it will, having just been minted for this body);
    3. run the **whole** policy rule set with the capability attached — P001,
       P003 and P004 get their say even though P002 is now satisfied;
    4. only then spend the capability, atomically, and deliver.

    A denial at step 3 leaves the capability unspent and the decision pending,
    because refusing is not the same as consuming the coordinator's approval.
    """
    decision = db.get_decision(decision_id)
    if decision is None:
        raise KeyError(decision_id)
    if decision.state is not DecisionState.PENDING:
        raise ApprovalError(f"decision is already {decision.state.value}")

    capability = mint(approver, decision.action, decision.audience, decision.body,
                      case_id=decision.case_id, decision_id=decision_id)

    ok, reason = capability.matches(decision.action, decision.audience, decision.body)
    if not ok:
        raise ApprovalError(reason)

    tainted = _tainted_for(decision)
    verdict = evaluate(
        decision.action,
        target=decision.audience,
        payload=decision.body,
        approval_token=capability.token,
        approver_role=APPROVAL_ROLE,
        reported_indicators=tainted,
        report_id="", case_id=decision.case_id, campaign_id=decision.campaign_id,
        actor=approver, origin="coordinator",
        broadcasts_in_last_24h=db.broadcasts_since(24),
    )
    if verdict.effect is Effect.FORBID:
        # The capability is left unspent on purpose. The coordinator's approval
        # was not consumed by a send that never happened.
        return {"allowed": False, "policy_id": verdict.policy_id, "reason": verdict.reason,
                "decision_id": decision_id}

    if not db.spend_approval_atomic(capability.token):
        raise ApprovalError("approval was already spent")

    channel = CHANNELS.get(decision.kind, "sandbox")
    outbox_id = db.append_outbox(OutboxMessage(
        decision_id=decision_id, channel=channel, audience=decision.audience,
        body=decision.body, approver=approver, sandbox=True))
    if decision.action in {"sms.send", "broadcast.send"}:
        db.record_broadcast(decision.audience)
    db.resolve_decision(decision_id, DecisionState.APPROVED, by=approver)
    db.append_audit(AuditEvent(
        action="delivery.sandbox", effect="permit", policy_id=verdict.policy_id,
        origin="coordinator", actor=approver, decision_id=decision_id,
        case_id=decision.case_id, campaign_id=decision.campaign_id, resource=decision.audience,
        reason=f"delivered to the sandbox outbox on channel '{channel}'"
               + (f"; note: {note}" if note else ""),
    ))
    return {"allowed": True, "decision_id": decision_id, "outbox_id": outbox_id,
            "channel": channel, "sandbox": True, "approver": approver,
            "policy_id": verdict.policy_id}


def redeem(token: str, action: str, audience: str, body: str) -> dict[str, Any]:
    """Redeem a raw capability directly. Used by the approval-abuse tests.

    Exists so the invariants can be attacked from outside the happy path: replay,
    substitution of audience or action, an edited body, an expired capability, a
    token that was never minted.
    """
    approval = db.get_approval(token)
    if approval is None:
        return {"allowed": False, "reason": "no such approval was ever issued",
                "policy_id": "P002"}
    ok, reason = approval.matches(action, audience, body)
    if not ok:
        return {"allowed": False, "reason": reason, "policy_id": "P002"}
    if not db.spend_approval_atomic(token):
        return {"allowed": False, "reason": "approval has already been spent",
                "policy_id": "P002"}
    return {"allowed": True, "approver": approval.approver}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _tainted_for(decision: Decision) -> list[str]:
    """Indicators that came out of reports on this case.

    Recomputed from the store rather than trusted from the decision, because the
    decision body is partly model-authored and is not an authority on what was
    reported.
    """
    values: set[str] = set()
    if decision.case_id:
        case = db.get_case(decision.case_id)
        if case:
            for rid in case.report_ids:
                for rec in db.correlation_records(decision.community_id,
                                                  settings().campaign_window_days):
                    if rec["report_id"] == rid:
                        values |= {k.split(":", 1)[1] for k in rec["indicator_keys"]}
    return sorted(values)


