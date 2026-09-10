"""The persistent domain model.

Kept separate from :mod:`porchlight.models`, which holds the agent I/O schemas —
those are Strands ``structured_output`` targets and their shape is a contract
with the model. These are the things the coalition actually owns and that must
survive a restart: reports, the cases they roll up into, the campaigns those
cases belong to, the decisions waiting for a human, the approvals that release
them, and the audit trail underneath all of it.

Two modelling choices are load-bearing.

**A case is not a report.** One resident filing three times about the same call
is one case, and two residents describing the same crew are two reports on
possibly one case. Conflating them inflates every "N residents affected" count,
which is the number a coordinator would act on. So `Case` is a container with its
own state and `case_reports` is a real relation.

**An approval is a capability, not a flag.** It names a human, an action, an
audience, and the exact bytes of the message it releases. Change any of those and
it no longer applies — see `Approval.matches`.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def message_digest(text: str) -> str:
    """Stable digest of an outbound message body.

    Whitespace-normalised, because a coordinator adding a trailing newline in a
    textarea has not changed the message, and forcing re-approval for that would
    train people to click through re-approvals — which is the failure mode this
    binding exists to prevent.
    """
    normalised = " ".join(text.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Reports and indicators
# --------------------------------------------------------------------------
class ReportState(str, Enum):
    QUEUED = "queued"          # accepted, not yet processed
    PROCESSED = "processed"    # ran the pipeline, attached to a case
    DUPLICATE = "duplicate"    # same resident, same substance, already on file
    FAILED = "failed"          # pipeline error; visible, never silently dropped
    NEEDS_REVIEW = "needs_review"  # processed but the agent was not confident


class IndicatorKind(str, Enum):
    PHONE = "phone"
    UPI = "upi"
    CRYPTO = "crypto"
    URL = "url"
    ACCOUNT = "acct"
    EMAIL = "email"
    DOMAIN = "domain"


class Indicator(BaseModel):
    """One extracted artefact, in both its raw and its comparable form.

    ``normalised`` is what correlation joins on; ``value`` is what a human reads.
    Keeping both means the audit trail can show the coordinator the number as the
    resident reported it while the clustering still matches +91 90000 00042
    against 9000000042.
    """

    report_id: str
    kind: IndicatorKind
    value: str
    normalised: str

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.normalised}"


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------
class CaseState(str, Enum):
    OPEN = "open"
    WATCHING = "watching"      # logged, nothing to do unless a pattern forms
    ESCALATED = "escalated"    # part of a campaign, or urgent on its own
    AWAITING_HUMAN = "awaiting_human"
    CLOSED = "closed"


class Case(BaseModel):
    """A coherent incident: one or more reports about the same thing."""

    case_id: str
    community_id: str
    opened_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    state: CaseState = CaseState.OPEN
    urgency: str = "green"
    # None means "we do not know", and it is rendered as "unknown" rather than
    # as a number. Inventing an hours-to-loss estimate would put a fabricated
    # figure at the top of a triage queue.
    hours_to_irreversible: Optional[float] = None
    script_fingerprint: str = ""
    impersonated_entity: str = ""
    money_rail: str = "none"
    reporter_pseudonym: str = ""
    reporter_area: str = ""
    campaign_id: str = ""
    summary: str = ""
    report_ids: list[str] = Field(default_factory=list)

    @property
    def report_count(self) -> int:
        return len(self.report_ids)


# --------------------------------------------------------------------------
# Campaigns
# --------------------------------------------------------------------------
class CampaignEvidence(BaseModel):
    """Why these reports were linked. Emitted by correlation, never by prose."""

    shared_indicators: list[str] = Field(default_factory=list)
    script_fingerprints: list[str] = Field(default_factory=list)
    distinct_reporters: int = 0
    areas: list[str] = Field(default_factory=list)
    span_days: int = 0
    # Per-pair provenance: which indicator linked which two reports. This is what
    # lets the UI answer "why is report 44 in here" without re-running anything.
    links: list[dict[str, str]] = Field(default_factory=list)


class Campaign(BaseModel):
    campaign_id: str
    community_id: str
    label: str = ""
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    confidence: float = 0.0
    evidence: CampaignEvidence = Field(default_factory=CampaignEvidence)
    report_ids: list[str] = Field(default_factory=list)
    # Set once, when the cluster first cleared threshold. The demo's "it just
    # fired" state is a stored fact, not a re-derivation at render time.
    escalated_at: Optional[datetime] = None
    escalated_by_report_id: str = ""


# --------------------------------------------------------------------------
# Decisions — the inbox
# --------------------------------------------------------------------------
class DecisionState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"   # the draft changed materially; re-decide


class Decision(BaseModel):
    """One thing a human must decide. The only thing that interrupts them."""

    decision_id: str
    community_id: str
    kind: str                       # matches DraftKind values
    action: str                     # the policy action it would perform
    title: str
    body: str
    audience: str
    rationale: str = ""             # what changed, in one sentence
    case_id: str = ""
    campaign_id: str = ""
    state: DecisionState = DecisionState.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None
    resolved_by: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def body_digest(self) -> str:
        return message_digest(self.body)


# --------------------------------------------------------------------------
# Approvals
# --------------------------------------------------------------------------
class Approval(BaseModel):
    """A single-use capability to perform one action, on one audience, once.

    Bound to the exact message. Editing the draft changes ``message_hash`` and
    the capability stops matching — which is the property that makes "approve"
    mean "approve *this*" rather than "approve this kind of thing".
    """

    token: str
    approver: str
    role: str
    action: str
    audience: str
    message_hash: str
    case_id: str = ""
    decision_id: str = ""
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: Optional[datetime] = None
    spent_at: Optional[datetime] = None

    @property
    def spent(self) -> bool:
        return self.spent_at is not None

    def expired(self, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or utcnow()) >= self.expires_at

    def matches(self, action: str, audience: str, body: str) -> tuple[bool, str]:
        """Does this capability authorise exactly this send?

        Returns ``(ok, reason)``. The reason is written for the audit trail, so
        it says which clause failed rather than just "denied".
        """
        if self.spent:
            return False, "approval has already been spent; each approval releases one send"
        if self.expired():
            return False, "approval has expired; approvals are deliberately short-lived"
        if self.action != action:
            return False, f"approval was granted for {self.action!r}, not {action!r}"
        if self.audience != audience:
            return False, f"approval was granted for audience {self.audience!r}, not {audience!r}"
        if self.message_hash != message_digest(body):
            return False, "message body changed after approval; the edited text was never approved"
        return True, "approval matches this action, audience and message"


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------
class AuditEvent(BaseModel):
    """Append-only. Answers: what was attempted, by whom, permitted or not, why."""

    event_id: Optional[int] = None
    at: datetime = Field(default_factory=utcnow)
    actor: str = "porchlight-agent"
    origin: str = "agent"           # agent | coordinator | untrusted-content | system
    action: str = ""
    effect: str = ""                # permit | forbid
    policy_id: str = ""
    reason: str = ""
    resource: str = ""
    report_id: str = ""
    case_id: str = ""
    campaign_id: str = ""
    decision_id: str = ""


# --------------------------------------------------------------------------
# Sandbox delivery
# --------------------------------------------------------------------------
class OutboxMessage(BaseModel):
    """A message that was 'delivered'. Nothing here ever leaves the process.

    The sandbox is not a stub standing in for a real sender that exists
    elsewhere — there is no real sender anywhere in this project, by design.
    """

    outbox_id: Optional[int] = None
    decision_id: str
    channel: str
    audience: str
    body: str
    delivered_at: datetime = Field(default_factory=utcnow)
    approver: str = ""
    sandbox: bool = True


# --------------------------------------------------------------------------
# Background jobs
# --------------------------------------------------------------------------
class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    job_id: Optional[int] = None
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    # Two submissions of the same artefact by the same reporter collapse to one
    # job. This is what makes replaying a batch safe.
    idempotency_key: str = ""
    state: JobState = JobState.PENDING
    attempts: int = 0
    last_error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
