"""The HTTP surface: what the coordinator actually touches.

Deliberately thin. Every decision — extraction, staging, clustering, drafting,
and above all the policy gate — already lives in :mod:`porchlight.pipeline` and
:mod:`porchlight.policy`. This module adds no judgement of its own; it moves
JSON, keeps the processed case files in memory so the queue can be re-rendered
without re-running the graph, and serves one static HTML file.

Two contracts are served from one app:

* the dashboard's own API (``/report``, ``/queue``, ``/campaigns``, ``/approve``)
* the AgentCore Runtime contract (``POST /invocations``, ``GET /ping`` on 8080),
  so ``agentcore launch`` can host this file unmodified.

One thing here *is* load-bearing: ``POST /approve`` is the only place in the
system where an approval token is minted, and it is minted from a named human.
That is what P002 checks for. The agent has no path to this endpoint.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import active_pack, settings
from .correlation import build_clusters, meets_threshold
from .models import DRAFT_ACTIONS, CaseFile, Channel, Report
from .pipeline import process_report
from .policy import backend_name, evaluate, mint_approval, recent_audit, reset_audit, spend_approval
from .tools.indicators import indicator_keys
from .tools.store import get_store, iter_records

log = logging.getLogger(__name__)

DASHBOARD = Path(__file__).parent / "dashboard" / "index.html"

# The pipeline mutates process-global state (the community store, the policy
# audit trail, the broadcast rate-limit window). FastAPI runs sync endpoints in
# a threadpool, so serialise report processing rather than let two reports
# interleave and corrupt a cluster count on camera.
_PIPELINE_LOCK = threading.Lock()

# Processed case files, newest last. Memory only: the durable record is the
# community store, which is what campaign correlation reads. Restarting the
# server empties the queue but preserves campaigns, which is the correct
# trade-off — the queue is a shift's worth of work, the store is the coalition's
# institutional memory.
_CASES: dict[str, CaseFile] = {}
_APPROVALS: dict[str, list[dict[str, Any]]] = {}

app = FastAPI(
    title="Porchlight",
    version="0.1.0",
    description="Community scam-campaign agent for local elder-fraud coalitions.",
)


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------
class ReportSubmission(BaseModel):
    """What the dashboard (or a partner system) posts.

    Only ``raw_content`` is required. Everything else has a sensible default so
    a volunteer can paste a message and press one key.
    """

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


class ApprovalRequest(BaseModel):
    """A named human taking responsibility for one outbound artefact."""

    report_id: str
    draft_kind: str
    approver: str = Field(min_length=1, description="Coordinator name. Recorded in the audit trail.")
    note: str = ""


# --------------------------------------------------------------------------
# Projections — the shapes the dashboard renders
# --------------------------------------------------------------------------
def _queue_item(case: CaseFile) -> dict[str, Any]:
    r, intake, stage, camp = case.report, case.intake, case.stage, case.campaign
    match = camp.match if camp and camp.is_campaign and camp.match else None
    approvals = _APPROVALS.get(r.report_id, [])
    return {
        "report_id": r.report_id,
        "received_at": r.received_at.isoformat(),
        "channel": r.channel.value,
        "reporter": r.reporter_pseudonym,
        "area": r.reporter_area,
        "urgency": stage.urgency.value if stage else "green",
        "hours_to_irreversible": stage.hours_to_irreversible if stage else None,
        "playbook_stage": stage.playbook_stage if stage else "",
        "recommended_human_action": stage.recommended_human_action if stage else "",
        "isolation_signals": stage.isolation_signals if stage else [],
        "script_fingerprint": intake.script_fingerprint if intake else "",
        # Sent so the queue can fall back to something a human can read when the
        # fingerprint is a sentinel ("unclassified script"). A row that says only
        # "unclassified" tells a coordinator nothing about whether to open it.
        "pretext": intake.pretext if intake else "",
        "impersonated_entity": intake.impersonated_entity if intake else "",
        "money_rail": intake.money_rail.value if intake else "none",
        "amount_demanded": intake.amount_demanded if intake else None,
        "currency": intake.currency if intake else "",
        "injection_flagged": bool(intake and intake.contains_injection_attempt),
        "campaign_id": match.campaign_id if match else "",
        "campaign_label": match.campaign_label if match else "",
        "newly_escalated": bool(camp and camp.newly_escalated),
        "decision_for_human": case.response.decision_for_human if case.response else "",
        "drafts_total": len(case.response.drafts) if case.response else 0,
        "drafts_approved": len(approvals),
        "errors": case.errors,
    }


def _case_detail(case: CaseFile) -> dict[str, Any]:
    """The full case file, plus the approval state the CaseFile does not carry."""
    payload = json.loads(case.model_dump_json())
    payload["approvals"] = _APPROVALS.get(case.report.report_id, [])
    payload["summary"] = _queue_item(case)
    return payload


def _sorted_cases() -> list[CaseFile]:
    return sorted(_CASES.values(), key=lambda c: c.sort_key)


def _tainted_values(case: CaseFile) -> list[str]:
    """Indicator strings that came out of a reported artefact.

    P001 refuses to let anything be sent *to* one of these. Recomputed here
    rather than trusted from the request, because the request is not trusted.
    """
    if not case.intake:
        return []
    return [k.split(":", 1)[1] for k in sorted(indicator_keys(case.intake.indicators))]


def _campaign_view() -> list[dict[str, Any]]:
    """Active campaigns, computed from the durable store rather than the queue.

    Uses exactly the clustering and threshold functions the pipeline uses, so
    the panel cannot show a campaign the pipeline would not have fired on.
    """
    s = settings()
    records = list(iter_records(s.community_id, s.campaign_window_days))
    by_id = {c.report.report_id: c for c in _CASES.values()}
    out: list[dict[str, Any]] = []

    for cluster in build_clusters(records):
        if not meets_threshold(cluster):
            continue
        label = (sorted(f for f in cluster.fingerprints if f) or ["unnamed script"])[0]
        bands: dict[str, int] = {}
        for rid in cluster.report_ids:
            case = by_id.get(rid)
            band = case.stage.urgency.value if case and case.stage else "green"
            bands[band] = bands.get(band, 0) + 1
        span = max((cluster.last_seen - cluster.first_seen).days, 1)
        out.append({
            "campaign_id": cluster.campaign_id,
            "campaign_label": f"Crew running '{label}'",
            "script_fingerprint": label,
            "member_report_ids": cluster.report_ids,
            "report_count": len(cluster.members),
            "distinct_reporters": cluster.distinct_reporters,
            "areas_affected": cluster.areas,
            "shared_indicators": sorted(cluster.shared_indicators),
            "first_seen": cluster.first_seen.isoformat(),
            "last_seen": cluster.last_seen.isoformat(),
            "span_days": span,
            "confidence": cluster.confidence(),
            "bands": bands,
            "why": (
                f"{len(cluster.members)} reports from {cluster.distinct_reporters} residents in "
                f"{span} day(s), sharing "
                f"{', '.join(sorted(cluster.shared_indicators)) or 'the same script'}"
                + (f", {len(cluster.areas)} area(s): {', '.join(cluster.areas)}."
                   if cluster.areas else ".")
            ),
        })

    out.sort(key=lambda c: (-c["report_count"], c["campaign_id"]))
    return out


# --------------------------------------------------------------------------
# The four endpoints
# --------------------------------------------------------------------------
@app.post("/report")
def post_report(submission: ReportSubmission) -> dict[str, Any]:
    """Run one report through the whole graph and return its case file."""
    report = submission.to_report()
    with _PIPELINE_LOCK:
        case = process_report(report)
        _CASES[report.report_id] = case
    return _case_detail(case)


@app.get("/queue")
def get_queue() -> dict[str, Any]:
    """The work, ordered by hours to irreversible loss — not by scam likelihood."""
    cases = _sorted_cases()
    counts: dict[str, int] = {"black": 0, "red": 0, "amber": 0, "green": 0}
    for c in cases:
        band = c.stage.urgency.value if c.stage else "green"
        counts[band] = counts.get(band, 0) + 1
    campaigns = _campaign_view()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "community_id": settings().community_id,
        "pack": active_pack().display_name,
        "offline": settings().offline,
        "policy_backend": backend_name(),
        "counts": counts,
        "total": len(cases),
        "campaign_count": len(campaigns),
        "injection_flagged": sum(
            1 for c in cases if c.intake and c.intake.contains_injection_attempt
        ),
        "items": [_queue_item(c) for c in cases],
    }


@app.get("/campaigns")
def get_campaigns() -> dict[str, Any]:
    """Clusters that cleared volume, breadth and evidence."""
    campaigns = _campaign_view()
    return {"count": len(campaigns), "campaigns": campaigns}


@app.post("/approve")
def post_approve(req: ApprovalRequest) -> dict[str, Any]:
    """Mint a coordinator approval token and re-evaluate the draft against policy.

    This is the human-in-the-loop, expressed as code rather than as a promise.
    The same Cedar rule set that denied the draft when the agent produced it is
    re-run here with an approval token attached — so P002 is satisfied by a
    named person, while P001 (never contact a reported endpoint), P003 (no raw
    identifiers) and P004 (broadcast rate limit) still apply and can still
    refuse. Approval is not an override.
    """
    case = _CASES.get(req.report_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown report {req.report_id}")
    if not case.response:
        raise HTTPException(status_code=409, detail="this case produced no drafts")

    draft = next((d for d in case.response.drafts if d.kind.value == req.draft_kind), None)
    if draft is None:
        raise HTTPException(
            status_code=404,
            detail=f"no {req.draft_kind!r} draft on {req.report_id}",
        )

    action = DRAFT_ACTIONS[draft.kind.value]
    # The token is minted by the policy module, scoped to this action, and
    # recorded against a named human. Nothing else in the system can produce one
    # that P002 will accept — which is what makes "a report cannot approve
    # itself" a property of the design rather than of the current call graph.
    token = mint_approval(req.approver, action, draft.intended_recipient)

    with _PIPELINE_LOCK:
        decision = evaluate(
            action,
            target=draft.intended_recipient,
            payload=draft.body,
            approval_token=token,
            reported_indicators=_tainted_values(case),
            report_id=req.report_id,
            campaign_id=(case.campaign.match.campaign_id
                         if case.campaign and case.campaign.match else ""),
            origin="coordinator",
        )
        if decision.allowed:
            # Only a permitted broadcast counts against the P004 window. An
            # attempt that was already refused must not consume the budget.
            if action in {"broadcast.send", "sms.send"}:
                from .policy import _BROADCAST_LOG  # noqa: PLC0415 — same package

                _BROADCAST_LOG.append(decision.at)
                spend_approval(token)
            draft.requires_approval = False
            _APPROVALS.setdefault(req.report_id, []).append({
                "draft_kind": draft.kind.value,
                "title": draft.title,
                "approver": req.approver,
                "note": req.note,
                "action": action,
                "policy_id": decision.policy_id,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                # The token itself is a capability. The audit trail names the
                # human and the decision; it does not reprint the credential.
                "token_issued": True,
            })
        case.policy_events = recent_audit(40)

    body = {
        "report_id": req.report_id,
        "draft_kind": draft.kind.value,
        "allowed": decision.allowed,
        "decision": decision.to_audit(),
        "approvals": _APPROVALS.get(req.report_id, []),
    }
    if not decision.allowed:
        # 403 with the reason attached: the refusal is the product, not an error.
        return JSONResponse(status_code=403, content=body)
    return body


# --------------------------------------------------------------------------
# Supporting endpoints — the dashboard's other two panels, and demo control
# --------------------------------------------------------------------------
@app.get("/audit")
def get_audit(limit: int = 40, effect: Optional[str] = None) -> dict[str, Any]:
    """The policy decision trail. This is what the camera points at.

    ``effect=forbid`` filters the *whole* trail and then takes the tail, rather
    than filtering a tail that has already been taken. That distinction is the
    difference between the dashboard's "Denied only" button showing the refusals
    and showing an empty panel: replaying a corpus writes hundreds of permits,
    and a few dozen of those are enough to push every denial out of the window.
    """
    from .policy import AUDIT, Effect

    n = max(1, min(limit, 500))
    if effect in {"permit", "forbid"}:
        wanted = Effect(effect)
        events = [d.to_audit() for d in AUDIT if d.effect is wanted][-n:]
    else:
        events = recent_audit(n)

    return {
        "backend": backend_name(),
        # The count is over the whole trail, not the returned page. "8 denied"
        # has to mean eight denials, not eight denials you can currently see.
        "denials": sum(1 for d in AUDIT if d.effect is Effect.FORBID),
        "returned": len(events),
        "events": list(reversed(events)),
    }


@app.get("/case/{report_id}")
def get_case(report_id: str) -> dict[str, Any]:
    case = _CASES.get(report_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown report {report_id}")
    return _case_detail(case)


@app.post("/reset")
def post_reset() -> dict[str, Any]:
    """Clear the queue, the community store and the audit trail.

    Present so a demo can be re-run from a clean slate without restarting the
    process or hunting for the JSON file.
    """
    with _PIPELINE_LOCK:
        _CASES.clear()
        _APPROVALS.clear()
        get_store().clear(settings().community_id)
        reset_audit()
    return {"ok": True, "detail": "queue, community store and audit trail cleared"}


@app.post("/replay")
def post_replay(
    directory: str = Body("corpus/seed", embed=True),
    limit: int = Body(0, embed=True),
    exclude: list[str] = Body(default_factory=list, embed=True),
    hold_campaign_tail: bool = Body(True, embed=True),
) -> dict[str, Any]:
    """Warm the store from a corpus directory.

    Campaign correlation only has anything to say once the community store holds
    history, so this is the demo's setup step — and doing it from the dashboard
    rather than a second terminal removes the most fragile moment in a recording.

    ``hold_campaign_tail`` defaults to **True**, and that default is deliberate.
    Loading the whole corpus puts the finished campaign on screen before the
    coordinator has done anything, which turns the one thing worth watching into
    a fact the page was already sitting on. With the tail held back, each planted
    campaign is left one report short of threshold — so the next report that
    arrives is what fires it, live.

    Which reports to hold is read from ``eval/labels.json`` (the corpus
    generator's own ground truth) rather than hard-coded, so regenerating the
    corpus cannot silently spoil the demo. ``exclude`` still works and is added
    on top for anything you want held back by hand.
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

    processed = 0
    with _PIPELINE_LOCK:
        for entry in raw:
            report = Report(**{k: v for k, v in entry.items() if not k.startswith("_")})
            if report.report_id in _CASES:
                continue
            _CASES[report.report_id] = process_report(report)
            processed += 1

    campaigns = _campaign_view()
    return {"ok": True, "processed": processed, "held_back": sorted(held),
            "queue_size": len(_CASES), "campaign_count": len(campaigns),
            "detail": (
                f"{len(held)} report(s) held back so the campaign is one short of "
                f"threshold. Submit one of them to fire it."
                if held else "whole corpus loaded; campaigns are already visible"
            )}


def _campaign_tail(labels_path: Path) -> set[str]:
    """Members to withhold so each planted campaign sits one report below threshold.

    Keeps ``campaign_min_reports - 1`` of each campaign's members and holds the
    rest. Returns an empty set if there is no ground truth to read, because a
    missing labels file is a reason to load everything, not to fail the demo.
    """
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("no usable %s; loading the whole corpus", labels_path)
        return set()
    keep = max(settings().campaign_min_reports - 1, 0)
    held: set[str] = set()
    for members in (labels.get("campaigns") or {}).values():
        held |= {str(m) for m in list(members)[keep:]}
    return held


# --------------------------------------------------------------------------
# AgentCore Runtime contract + static dashboard
# --------------------------------------------------------------------------
@app.get("/ping")
def ping() -> dict[str, str]:
    """AgentCore Runtime health probe."""
    return {"status": "Healthy"}


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "offline": settings().offline,
        "pack": settings().pack_name,
        "policy_backend": backend_name(),
        "queue_size": len(_CASES),
    }


@app.post("/invocations")
def invocations(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """AgentCore Runtime entry point.

    Accepts either a full report body or the ``{"prompt": "..."}`` shape the
    runtime sends by default, so the same container answers both the dashboard
    and an AgentCore invocation.
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
    return post_report(submission)


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
