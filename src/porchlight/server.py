"""The HTTP surface: what the coordinator actually touches.

Deliberately thin. Ingestion lives in :mod:`porchlight.ingest`, the decision and
approval machinery in :mod:`porchlight.approvals`, enforcement in
:mod:`porchlight.policy`, persistence in :mod:`porchlight.db`. This module moves
JSON and serves one HTML file.

Three contracts are served from one app:

* **the decision inbox** the coordinator uses (``/inbox``, ``/decisions/...``);
* **the ingestion webhook** partner systems post to (``POST /reports``), which is
  the same code path the replay source uses — so the demo exercises production
  ingestion rather than a shortcut;
* **the AgentCore Runtime contract** (``POST /invocations``, ``GET /ping``), so a
  container can host this file unmodified.

The load-bearing thing here: no endpoint in this file can send anything. Delivery
happens only through ``approvals.approve``, only into the sandbox outbox, and
only with a capability minted against a named human.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import approvals, db, ingest
from .config import active_pack, settings
from .domain import CaseState, DecisionState, ReportState, utcnow
from .models import Channel, Report
from .policy import backend_name
from .tools import fixtures

log = logging.getLogger(__name__)

DASHBOARD = Path(__file__).parent / "dashboard" / "index.html"

# When the coordinator last looked. Drives "what happened while you were away".
# Process-local on purpose: after a restart the honest answer is "since this
# session started", and persisting it would imply a per-user identity this
# deployment does not have.
_LAST_SEEN: dict[str, datetime] = {}
_STARTED_AT = utcnow()

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Open the store and start the background worker; stop it on the way out.

    The worker is what makes this a background agent rather than a form: reports
    are processed whether or not anyone has the dashboard open.
    """
    db.connect()
    ingest.worker().start()
    yield
    ingest.worker().stop()


app = FastAPI(
    title="Porchlight",
    version="0.2.0",
    description="Background intake agent for local elder-fraud response networks.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# Dev authentication
# --------------------------------------------------------------------------
def coordinator(x_coordinator: str = Header(default="")) -> str:
    """Identify the approving human.

    **This is development authentication, not identity.** It records *which*
    coordinator approved something; it does not verify *that* they are that
    person. A real deployment puts an IdP in front of this and the approval
    capability binds to the authenticated subject. Labelled everywhere it
    appears, because an unlabelled fake login is worse than an obvious one.
    """
    name = (x_coordinator or "").strip()
    if not name:
        raise HTTPException(status_code=401,
                            detail="X-Coordinator header required (dev auth: names the approver)")
    return name


def _ingest_authorised(x_ingest_token: str = Header(default="")) -> bool:
    expected = settings().ingest_token
    if not expected:
        return True  # open webhook; surfaced in /inbox as such
    if x_ingest_token != expected:
        raise HTTPException(status_code=401, detail="invalid ingest token")
    return True


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------
class ReportSubmission(BaseModel):
    """What a partner system, the dashboard, or a replay posts."""

    raw_content: str = Field(min_length=1)
    channel: Channel = Channel.SMS
    volunteer_note: str = ""
    reporter_pseudonym: str = "resident"
    reporter_area: str = ""
    report_id: Optional[str] = None
    community_id: Optional[str] = None
    received_at: Optional[datetime] = None

    def to_report(self) -> Report:
        s = settings()
        return Report(
            report_id=self.report_id or f"rpt_{uuid.uuid4().hex[:8]}",
            community_id=self.community_id or s.community_id,
            received_at=self.received_at or datetime.now(timezone.utc),
            channel=self.channel,
            raw_content=self.raw_content,
            volunteer_note=self.volunteer_note,
            reporter_pseudonym=self.reporter_pseudonym or "resident",
            reporter_area=self.reporter_area,
        )


class DecisionEdit(BaseModel):
    body: str = Field(min_length=1)
    title: Optional[str] = None


class DecisionNote(BaseModel):
    note: str = ""


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
@app.post("/reports", status_code=202)
def post_reports(submission: ReportSubmission,
                 _: bool = Depends(_ingest_authorised)) -> dict[str, Any]:
    """Accept a report and queue it. Returns immediately; the worker does the work.

    202 rather than 200 on purpose: nothing has been analysed yet, and pretending
    otherwise would make the response a promise the system has not kept.
    """
    result = ingest.accept(submission.to_report())
    if not result["accepted"]:
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


@app.post("/replay")
def post_replay(
    directory: str = Body("corpus/seed", embed=True),
    limit: int = Body(0, embed=True),
    exclude: list[str] = Body(default_factory=list, embed=True),
    hold_campaign_tail: bool = Body(True, embed=True),
    wait: bool = Body(True, embed=True),
) -> dict[str, Any]:
    """Feed a corpus through the *same* ingestion path a webhook would use.

    ``hold_campaign_tail`` defaults to True. Loading the whole corpus puts the
    finished campaign on screen before the coordinator has done anything, which
    turns the one thing worth watching into a fact the page was already sitting
    on. Held back, each planted campaign sits one report short of threshold, and
    the next report to arrive is what fires it.

    Which reports to hold is read from the corpus generator's own ground truth
    rather than hard-coded, so regenerating the corpus cannot spoil the demo.
    """
    from .config import REPO_ROOT

    path = (REPO_ROOT / directory) if not Path(directory).is_absolute() else Path(directory)
    source = path / "reports.json"
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"no reports.json under {path}")

    raw = json.loads(source.read_text(encoding="utf-8"))
    held = {str(r) for r in exclude}
    if hold_campaign_tail:
        held |= _campaign_tail(REPO_ROOT / "eval" / "labels.json")
    if held:
        raw = [r for r in raw if r.get("report_id") not in held]
    if limit > 0:
        raw = raw[:limit]

    queued = 0
    for entry in raw:
        report = Report(**{k: v for k, v in entry.items() if not k.startswith("_")})
        if ingest.accept(report).get("queued"):
            queued += 1

    tally = ingest.drain() if wait else {}
    return {
        "ok": True, "queued": queued, "held_back": sorted(held), "processed": tally,
        "campaign_count": len(db.list_campaigns(settings().community_id)),
        "detail": (f"{len(held)} report(s) held back so the campaign sits one short of "
                   f"threshold. Submit one of them to fire it."
                   if held else "whole corpus loaded; campaigns are already visible"),
    }


def _campaign_tail(labels_path: Path) -> set[str]:
    """Members to withhold so that *no* crew starts above the campaign threshold.

    Trims every crew in the corpus's ground truth to one report below threshold,
    not just the labelled headline campaign. The corpus deliberately contains a
    second crew as a near-miss distractor, and that crew is a real one — it
    clears the bar honestly. Holding back only the headline left the board
    opening with a campaign and a waiting decision already on it, which turns
    "here is the one thing that needs you" into "here are two, one of which was
    already there".

    Note what this is *not*: the reports are withheld from the queue, not marked
    or special-cased. Everything held back is a normal report that the normal
    pipeline has never seen, so the campaign that forms when one arrives is
    found rather than replayed.
    """
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("no usable %s; loading the whole corpus", labels_path)
        return set()

    keep = max(settings().campaign_min_reports - 1, 0)
    held: set[str] = set()

    by_crew: dict[str, list[str]] = {}
    for report_id, crew in (labels.get("crews") or {}).items():
        if crew and crew != "none":
            by_crew.setdefault(crew, []).append(str(report_id))
    for members in by_crew.values():
        if len(members) > keep:
            held |= set(sorted(members)[keep:])

    # Fall back to the labelled campaigns if crew truth is absent (older corpus).
    if not by_crew:
        for members in (labels.get("campaigns") or {}).values():
            held |= {str(m) for m in list(members)[keep:]}
    return held


# --------------------------------------------------------------------------
# The decision inbox
# --------------------------------------------------------------------------
def _mode_badges() -> dict[str, Any]:
    s = settings()
    return {
        "model": "Offline demo" if s.offline else "Live model",
        "tools": "Fixture-backed tools" if fixtures.enabled() else "Live feeds",
        "delivery": "Sandbox delivery",
        "policy_backend": backend_name(),
        "auth": "Dev auth — records who approved, does not verify identity",
        "ingest": "Open webhook" if not s.ingest_token else "Token-protected webhook",
        "pack": active_pack().display_name,
    }


def _case_view(case: Any) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "state": case.state.value,
        "urgency": case.urgency,
        # None renders as "unknown". Inventing a number here would put a
        # fabricated figure at the top of a triage queue.
        "hours_to_irreversible": case.hours_to_irreversible,
        "script_fingerprint": case.script_fingerprint,
        "impersonated_entity": case.impersonated_entity,
        "money_rail": case.money_rail,
        "reporter": case.reporter_pseudonym,
        "area": case.reporter_area,
        "campaign_id": case.campaign_id,
        "summary": case.summary,
        "report_count": case.report_count,
        "report_ids": case.report_ids,
        "updated_at": case.updated_at.isoformat(),
    }


def _decision_view(decision: Any) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "kind": decision.kind,
        "action": decision.action,
        "title": decision.title,
        "body": decision.body,
        "audience": decision.audience,
        "rationale": decision.rationale,
        "case_id": decision.case_id,
        "campaign_id": decision.campaign_id,
        "state": decision.state.value,
        "created_at": decision.created_at.isoformat(),
        "resolved_by": decision.resolved_by,
        "evidence": decision.evidence,
    }


def _campaign_view(campaign: Any) -> dict[str, Any]:
    ev = campaign.evidence
    return {
        "campaign_id": campaign.campaign_id,
        "label": campaign.label,
        "report_count": len(campaign.report_ids),
        "report_ids": campaign.report_ids,
        "distinct_reporters": ev.distinct_reporters,
        "shared_indicators": ev.shared_indicators,
        "script_fingerprints": ev.script_fingerprints,
        "areas": ev.areas,
        "span_days": ev.span_days,
        "confidence": campaign.confidence,
        "links": ev.links,
        "first_seen": campaign.first_seen.isoformat(),
        "last_seen": campaign.last_seen.isoformat(),
        "escalated_by_report_id": campaign.escalated_by_report_id,
    }


# What the agent did, phrased for the person who was not watching it happen.
# The audit table stores an action id; this is the only place it becomes English,
# so a wording change happens once.
_ACTIVITY_WORDING = {
    "report.dedup": "Repeat report identified",
    "report.processed": "Report processed",
    "case.merge": "New evidence added to an existing case",
    "campaign.escalate": "Reports connected — prepared for review",
    "decision.raise": "Warning drafted for review",
    "decision.edit": "Draft edited",
    "decision.reject": "Draft set aside",
    "delivery.sandbox": "Approved and sent to the sandbox",
    "report.process": "Report could not be processed",
}


def _activity_view(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "at": event["at"],
        "action": event["action"],
        "headline": _ACTIVITY_WORDING.get(event["action"], event["action"]),
        "detail": event["reason"],
        "report_id": event["report_id"],
        "case_id": event["case_id"],
        "campaign_id": event["campaign_id"],
        "decision_id": event["decision_id"],
        # A failure is the one activity line that should not read like routine work.
        "is_problem": event["effect"] == "forbid" and event["action"] == "report.process",
    }


@app.get("/activity")
def get_activity(limit: int = 60) -> dict[str, Any]:
    """The work log: what the agent did, in the order it happened.

    Distinct from ``/audit``, which answers what was *attempted* and whether
    policy permitted it. Different question, different reader.
    """
    events = db.list_activity(settings().community_id, limit=max(1, min(limit, 200)))
    return {"count": len(events), "events": [_activity_view(e) for e in events]}


@app.get("/inbox")
def get_inbox(mark_seen: bool = True,
              who: str = Header(default="coordinator", alias="X-Coordinator")) -> dict[str, Any]:
    """The default screen. Answers, in order:

    1. what Porchlight handled while nobody was watching;
    2. what needs a decision;
    3. everything else, as a case list.
    """
    s = settings()
    since = _LAST_SEEN.get(who, _STARTED_AT)
    cases = db.list_cases(s.community_id)
    decisions = db.list_decisions(s.community_id, DecisionState.PENDING)
    campaigns = db.list_campaigns(s.community_id)

    # Two sets of numbers, kept apart on purpose. "Since your last visit" has to
    # mean that; reporting an all-time total under that heading is the kind of
    # small dishonesty a coordinator notices the first time the number will not
    # go down, and after that they trust none of the others either.
    handled = {
        "since": since.isoformat(),
        "is_first_visit": who not in _LAST_SEEN,
        "processed": db.count_reports(s.community_id, ReportState.PROCESSED, since=since),
        "duplicates_folded": db.count_reports(s.community_id, ReportState.DUPLICATE, since=since),
        "needs_review": db.count_reports(s.community_id, ReportState.NEEDS_REVIEW, since=since),
        "failed": db.count_reports(s.community_id, ReportState.FAILED, since=since),
    }
    totals = {
        "processed": db.count_reports(s.community_id, ReportState.PROCESSED),
        "duplicates_folded": db.count_reports(s.community_id, ReportState.DUPLICATE),
        "needs_review": db.count_reports(s.community_id, ReportState.NEEDS_REVIEW),
        "failed": db.count_reports(s.community_id, ReportState.FAILED),
        "cases_open": sum(1 for c in cases if c.state is not CaseState.CLOSED),
        "cases_merged": sum(1 for c in cases if c.report_count > 1),
        "campaigns_found": len(campaigns),
        "escalated": sum(1 for c in cases if c.state is CaseState.ESCALATED),
        "policy_denials": db.count_audit("forbid"),
    }
    activity = [_activity_view(e) for e in db.list_activity(s.community_id, limit=12)]
    if mark_seen:
        _LAST_SEEN[who] = utcnow()

    bands = {"black": 0, "red": 0, "amber": 0, "green": 0}
    for case in cases:
        bands[case.urgency] = bands.get(case.urgency, 0) + 1

    return {
        "generated_at": utcnow().isoformat(),
        "community_id": s.community_id,
        "modes": _mode_badges(),
        "handled_while_away": handled,
        "totals": totals,
        "activity": activity,
        "decisions": [_decision_view(d) for d in decisions],
        "bands": bands,
        "cases": [_case_view(c) for c in _sorted_cases(cases)],
        "campaigns": [_campaign_view(c) for c in campaigns],
        "worker_running": ingest.worker().running,
        "jobs": db.job_counts(),
        "failed_jobs": db.list_failed_jobs(5),
    }


def _sorted_cases(cases: list[Any]) -> list[Any]:
    """Sorted by distance to irreversible loss, not by scam likelihood.

    Unknown hours sort last within their band rather than being treated as zero —
    "we do not know" is not "we have plenty of time", but it is also not a reason
    to outrank a case with a measured two-hour window.
    """
    order = {"black": 0, "red": 1, "amber": 2, "green": 3}
    return sorted(cases, key=lambda c: (order.get(c.urgency, 4),
                                        c.hours_to_irreversible
                                        if c.hours_to_irreversible is not None else 1e9))


@app.get("/cases")
def get_cases() -> dict[str, Any]:
    cases = db.list_cases(settings().community_id)
    return {"count": len(cases), "cases": [_case_view(c) for c in _sorted_cases(cases)]}


@app.get("/case/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case {case_id}")
    reports = [db.get_report(rid) for rid in case.report_ids]
    return {
        **_case_view(case),
        "reports": [r for r in reports if r],
        "campaign": (_campaign_view(db.get_campaign(case.campaign_id))
                     if case.campaign_id and db.get_campaign(case.campaign_id) else None),
    }


@app.get("/campaigns")
def get_campaigns() -> dict[str, Any]:
    campaigns = db.list_campaigns(settings().community_id)
    return {"count": len(campaigns), "campaigns": [_campaign_view(c) for c in campaigns]}


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------
@app.get("/decisions")
def get_decisions(state: Optional[str] = None) -> dict[str, Any]:
    wanted = DecisionState(state) if state else None
    decisions = db.list_decisions(settings().community_id, wanted)
    return {"count": len(decisions), "decisions": [_decision_view(d) for d in decisions]}


@app.get("/decision/{decision_id}")
def get_decision(decision_id: str) -> dict[str, Any]:
    """Everything the review screen needs to make one judgement.

    Assembled server-side so the reviewer is looking at one consistent snapshot.
    Four round-trips stitched together in the browser can show evidence from
    before an edit next to a draft from after it.
    """
    decision = db.get_decision(decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"unknown decision {decision_id}")

    campaign = db.get_campaign(decision.campaign_id) if decision.campaign_id else None
    member_ids = list(campaign.report_ids) if campaign else []
    reports = [r for r in (db.get_report(rid) for rid in member_ids) if r]

    return {
        **_decision_view(decision),
        "campaign": _campaign_view(campaign) if campaign else None,
        "reports": [{
            "report_id": r["report_id"],
            "reporter": r["reporter_pseudonym"],
            "area": r["reporter_area"],
            "received_at": r["received_at"],
            "channel": r["channel"],
            "state": r["state"],
            "script_fingerprint": r["script_fingerprint"],
            "urgency": r["urgency"],
            "raw_content": r["raw_content"],
        } for r in reports],
        # Said plainly rather than left to be inferred from an absence.
        "uncertain": _what_is_not_known(decision, campaign, reports),
        "delivered": db.get_decision(decision_id).state is DecisionState.APPROVED,
    }


def _what_is_not_known(decision: Any, campaign: Any, reports: list[dict[str, Any]]) -> list[str]:
    """The limits of the evidence, stated in the briefing rather than omitted.

    A reviewer deciding whether to warn a neighbourhood needs the gaps as much as
    the findings. Leaving them out makes a correlation look like a conclusion.
    """
    gaps: list[str] = []
    if campaign is None:
        gaps.append("This draft is not attached to a correlated group of reports.")
        return gaps

    ev = campaign.evidence
    if not ev.shared_indicators:
        gaps.append("No shared callback number or payment destination — "
                    "these reports are linked by script wording alone.")
    if len(ev.areas) > 1:
        gaps.append(f"Reports come from {len(ev.areas)} areas, so the affected list "
                    f"may be wider or narrower than one neighbourhood.")
    if ev.distinct_reporters < 4:
        gaps.append(f"{ev.distinct_reporters} residents reported this. A larger group "
                    f"would make the pattern more certain.")
    if any(r["state"] == "needs_review" for r in reports):
        gaps.append("At least one linked report could not be classified automatically.")

    gaps.append("Shared details show these reports are related. They do not "
                "establish who is responsible.")
    return gaps


@app.patch("/decisions/{decision_id}")
def patch_decision(decision_id: str, edit: DecisionEdit,
                   who: str = Depends(coordinator)) -> dict[str, Any]:
    """Edit a draft before approving it.

    No approval is revoked here and none needs to be: a capability is bound to
    the message digest, so an edited body simply stops matching. Invalidation is
    a property of the data model, not a cleanup step someone must remember.
    """
    try:
        decision = approvals.edit_decision(decision_id, edit.body, edit.title)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown decision {decision_id}") from None
    return _decision_view(decision)


@app.post("/decisions/{decision_id}/approve")
def post_approve(decision_id: str, note: DecisionNote = DecisionNote(),
                 who: str = Depends(coordinator)) -> dict[str, Any]:
    """Approve one decision. The only path in the system that delivers anything.

    A policy denial comes back as 403 with the rule that refused it. The refusal
    is the product, not an error — and the coordinator's approval is not consumed
    by a send that never happened.
    """
    try:
        result = approvals.approve(decision_id, who, note.note)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown decision {decision_id}") from None
    except approvals.ApprovalError as exc:
        return JSONResponse(status_code=409, content={"allowed": False, "reason": exc.reason,
                                                      "policy_id": exc.policy_id})
    if not result["allowed"]:
        return JSONResponse(status_code=403, content=result)
    return result


@app.post("/decisions/{decision_id}/reject")
def post_reject(decision_id: str, note: DecisionNote = DecisionNote(),
                who: str = Depends(coordinator)) -> dict[str, Any]:
    try:
        decision = approvals.reject_decision(decision_id, who, note.note)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown decision {decision_id}") from None
    return _decision_view(decision)


# --------------------------------------------------------------------------
# Audit, outbox, reset
# --------------------------------------------------------------------------
@app.get("/audit")
def get_audit(limit: int = 40, effect: Optional[str] = None) -> dict[str, Any]:
    """The policy decision trail.

    ``effect=forbid`` filters the whole trail in SQL and then takes the tail,
    rather than filtering a tail already taken. That distinction is the
    difference between "Denied only" showing the refusals and showing an empty
    panel: a replay writes hundreds of permits.
    """
    n = max(1, min(limit, 500))
    return {
        "backend": backend_name(),
        "denials": db.count_audit("forbid"),
        "total": db.count_audit(),
        "events": db.list_audit(n, effect),
    }


@app.get("/outbox")
def get_outbox(limit: int = 50) -> dict[str, Any]:
    """Everything that was 'delivered'. All of it sandboxed, all of it labelled."""
    messages = db.list_outbox(limit)
    return {"count": len(messages), "sandbox": True,
            "note": "Porchlight has no real sender. This is the whole delivery surface.",
            "messages": messages}


@app.post("/reset")
def post_reset() -> dict[str, Any]:
    """Clear everything so the demo can be re-run without editing files."""
    db.reset()
    _LAST_SEEN.clear()
    from .policy import reset_audit
    from .tools.store import get_store

    get_store().clear(settings().community_id)
    reset_audit()
    return {"ok": True, "detail": "reports, cases, campaigns, decisions, approvals, "
                                  "audit trail and sandbox outbox cleared"}


# --------------------------------------------------------------------------
# AgentCore Runtime contract + static dashboard
# --------------------------------------------------------------------------
@app.get("/ping")
def ping() -> dict[str, str]:
    """AgentCore Runtime health probe."""
    return {"status": "Healthy"}


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    s = settings()
    return {
        "status": "ok",
        "offline": s.offline,
        "pack": s.pack_name,
        "policy_backend": backend_name(),
        "worker_running": ingest.worker().running,
        "cases": len(db.list_cases(s.community_id)),
        "jobs": db.job_counts(),
    }


@app.post("/invocations")
def invocations(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """AgentCore Runtime entry point.

    Synchronous, unlike ``POST /reports``: the Runtime contract expects a result
    in the response, so this accepts, drains, and returns the finished case
    rather than a 202 the caller cannot act on.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    text = payload.get("raw_content") or payload.get("prompt") or payload.get("input")
    if not text or not str(text).strip():
        raise HTTPException(status_code=400, detail="expected 'raw_content' or 'prompt'")

    submission = ReportSubmission(
        raw_content=str(text),
        channel=Channel(payload["channel"]) if payload.get("channel") else Channel.OTHER,
        volunteer_note=str(payload.get("volunteer_note", "")),
        reporter_pseudonym=str(payload.get("reporter_pseudonym", "resident")),
        reporter_area=str(payload.get("reporter_area", "")),
        report_id=payload.get("report_id"),
    )
    report = submission.to_report()
    accepted = ingest.accept(report)
    ingest.drain()

    row = db.get_report(report.report_id) or {}
    case = db.get_case(row.get("case_id") or "") if row.get("case_id") else None
    return {
        "report_id": report.report_id,
        "accepted": accepted,
        "state": row.get("state"),
        "case": _case_view(case) if case else None,
        "campaign": (_campaign_view(db.get_campaign(case.campaign_id))
                     if case and case.campaign_id and db.get_campaign(case.campaign_id)
                     else None),
        "pending_decisions": len(db.list_decisions(settings().community_id,
                                                   DecisionState.PENDING)),
    }


@app.get("/fonts/{filename}")
def font(filename: str) -> FileResponse:
    """Serve the vendored typefaces.

    Self-hosted rather than from a CDN: the container has no guaranteed outbound
    network, and a screen about someone's fraud report should not make a
    third-party request every time it loads. See dashboard/fonts/README.md for
    licences.
    """
    if not re.fullmatch(r"[a-z0-9-]+\.woff2", filename):
        raise HTTPException(status_code=404, detail="not found")
    path = DASHBOARD.parent / "fonts" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="font/woff2",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/")
def dashboard() -> FileResponse:
    if not DASHBOARD.exists():  # pragma: no cover — packaging failure
        raise HTTPException(status_code=500, detail="dashboard/index.html is missing")
    return FileResponse(DASHBOARD, media_type="text/html")


def main() -> None:  # pragma: no cover — process entry point
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":  # pragma: no cover
    main()
