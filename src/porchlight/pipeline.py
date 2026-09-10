"""The orchestrator: one report in, one case file out.

Two execution modes, one code path for everything that matters:

* ``PORCHLIGHT_OFFLINE=1`` — deterministic node implementations. Free, fast,
  reproducible. Used by tests, CI and iteration on clustering.
* otherwise — the real Strands graph: agents with structured output, a Swarm for
  stage assessment, tools behind the policy boundary.

Clustering and policy are identical in both modes, because those are the parts
the submission actually rests on and they must not be able to drift.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .config import settings
from .models import DRAFT_ACTIONS, CaseFile, Report
from .policy import PolicyDenied, enforce, recent_audit
from .prompts import STAGE_TASK_TEMPLATE, envelope
from .tools.indicators import extract_indicators, indicator_keys, merge_indicators
from .tools.sanitize import requested_capabilities, scrub_pii
from .tools.store import record_processed

log = logging.getLogger(__name__)


def _dump(obj: Any) -> str:
    return json.dumps(obj.model_dump(mode="json"), indent=2, default=str) if obj else "{}"


def process_report(report: Report, *, offline: bool | None = None) -> CaseFile:
    """Run one report through the full pipeline."""
    s = settings()
    use_offline = s.offline if offline is None else offline
    case = CaseFile(report=report)
    t0 = time.perf_counter()

    # Defensive pre-processing. Applied before any model sees the artefact.
    report = report.model_copy(update={"raw_content": scrub_pii(report.raw_content)})
    case.report = report

    try:
        if use_offline:
            _run_offline(case, report)
        else:
            _run_agents(case, report)
    except PolicyDenied as denied:
        case.errors.append(f"policy denied: {denied}")
    except Exception as exc:  # noqa: BLE001 — one bad report must not stop the queue
        log.exception("pipeline failure on %s", report.report_id)
        case.errors.append(f"{type(exc).__name__}: {exc}")

    # Persist to community memory so the NEXT report can correlate against this
    # one. _provisional_record already inserted it before clustering ran, so this
    # is an update of the urgency band, not a second insert — double-inserting
    # would inflate every cluster and every "N residents affected" count.
    if case.intake:
        enforce("store.write", target=report.report_id, payload=_dump(case.intake),
                reported_indicators=[], report_id=report.report_id)
        _finalise_record(case, report)

    case.policy_events = recent_audit(40)
    log.info("processed %s in %.2fs", report.report_id, time.perf_counter() - t0)
    return case


# --------------------------------------------------------------------------
def _run_offline(case: CaseFile, report: Report) -> None:
    from . import offline

    case.intake = offline.offline_intake(report)
    case.corroboration = offline.offline_corroboration(report, case.intake)
    case.stage = offline.offline_stage(report, case.intake)
    # Correlation must see THIS report, so persist a provisional record first.
    _provisional_record(case, report)
    case.campaign = offline.offline_campaign(report, case.intake)
    case.response = offline.offline_response(report, case.intake, case.stage, case.campaign)
    _check_drafts(case)
    _record_refused_requests(case)


def _run_agents(case: CaseFile, report: Report) -> None:
    from .agents import (
        build_campaign_agent,
        build_corroboration_agent,
        build_intake_agent,
        build_response_agent,
        build_stage_swarm,
    )
    from .config import active_pack

    art = envelope(report.report_id, report.raw_content)
    pack = active_pack()

    # --- Node 1: intake -------------------------------------------------
    intake = build_intake_agent().structured_output(
        type(case).model_fields["intake"].annotation.__args__[0],  # IntakeResult
        f"Extract the record from this report.\n\nChannel: {report.channel.value}\n"
        f"Volunteer note (semi-trusted): {report.volunteer_note}\n\n{art}",
    )
    # Union the model's extraction with the deterministic one. Neither is complete.
    intake.indicators = merge_indicators(
        intake.indicators, extract_indicators(f"{report.raw_content}\n{report.volunteer_note}")
    )
    case.intake = intake

    # --- Node 2: corroboration -----------------------------------------
    from .models import CorroborationResult

    keys = sorted(indicator_keys(intake.indicators))
    case.corroboration = build_corroboration_agent().structured_output(
        CorroborationResult,
        "Corroborate this report using your tools. Check only these indicators.\n\n"
        f"script_fingerprint: {intake.script_fingerprint}\n"
        f"indicators: {json.dumps(keys)}\n\n"
        f"Structured intake (trusted):\n{_dump(intake)}",
    )

    # --- Node 3: stage swarm -------------------------------------------
    from .models import StageAssessment

    swarm_task = STAGE_TASK_TEMPLATE.format(
        intake_json=_dump(intake),
        corroboration_json=_dump(case.corroboration),
        volunteer_note=report.volunteer_note or "(none)",
        artefact_envelope=art,
    )
    swarm_result = build_stage_swarm()(swarm_task)
    case.stage = _extract_structured(swarm_result, StageAssessment)

    # --- Node 4: campaign ----------------------------------------------
    _provisional_record(case, report)
    from .models import CampaignResult

    case.campaign = build_campaign_agent().structured_output(
        CampaignResult,
        f"Judge whether report {report.report_id} belongs to a real campaign. "
        f"Call find_candidate_cluster('{report.report_id}') first.\n\n"
        f"This report's intake:\n{_dump(intake)}",
    )

    # --- Node 5: response ----------------------------------------------
    from .models import ResponseResult

    case.response = build_response_agent().structured_output(
        ResponseResult,
        "Draft what this case needs. Draft nothing it does not need.\n\n"
        f"Jurisdiction pack: {json.dumps({'name': pack.display_name, 'reporting': pack.reporting, 'partners': pack.partners}, default=str)}\n\n"
        f"Intake:\n{_dump(intake)}\n\nStage:\n{_dump(case.stage)}\n\nCampaign:\n{_dump(case.campaign)}",
    )
    _check_drafts(case)
    _record_refused_requests(case)


def _extract_structured(swarm_result: Any, model_cls):
    """Pull the final structured assessment out of a Swarm result."""
    for node_id in reversed(getattr(swarm_result, "execution_order", []) or []):
        nid = getattr(node_id, "node_id", node_id)
        node = (getattr(swarm_result, "results", {}) or {}).get(nid)
        payload = getattr(node, "result", None)
        if isinstance(payload, model_cls):
            return payload
        structured = getattr(node, "structured_output", None)
        if isinstance(structured, model_cls):
            return structured
    raise RuntimeError("stage swarm produced no StageAssessment")


def _finalise_record(case: CaseFile, report: Report) -> None:
    """Update the stored record in place with the final band."""
    from .tools.store import get_store

    s = settings()
    store = get_store()
    for rec in store._data.get(s.community_id, []):  # noqa: SLF001 - same package
        if rec["report_id"] == report.report_id:
            rec["urgency"] = case.stage.urgency.value if case.stage else "green"
            rec["campaign_id"] = (
                case.campaign.match.campaign_id
                if case.campaign and case.campaign.match else ""
            )
            store._flush()  # noqa: SLF001
            return
    _provisional_record(case, report)


def _provisional_record(case: CaseFile, report: Report) -> None:
    """Make this report visible to the clustering pass that is about to run."""
    from .tools.store import get_store

    s = settings()
    existing = {r["report_id"] for r in get_store().all(s.community_id)}
    if report.report_id in existing or not case.intake:
        return
    record_processed(s.community_id, {
        "report_id": report.report_id,
        "reporter_pseudonym": report.reporter_pseudonym,
        "reporter_area": report.reporter_area,
        "received_at": report.received_at.isoformat(),
        "script_fingerprint": case.intake.script_fingerprint,
        "indicator_keys": sorted(indicator_keys(case.intake.indicators)),
        "money_rail": case.intake.money_rail.value,
        "urgency": case.stage.urgency.value if case.stage else "green",
    })


def _tainted_values(case: CaseFile) -> list[str]:
    """Indicator strings that came out of the reported artefact."""
    if not case.intake:
        return []
    return [k.split(":", 1)[1] for k in sorted(indicator_keys(case.intake.indicators))]


def _check_drafts(case: CaseFile) -> None:
    """Every draft passes the policy boundary before it can be shown as sendable.

    Nothing here sends. This proves, in the audit trail, that the send path is
    closed unless a coordinator supplies an approval token.
    """
    if not case.response:
        return
    from .policy import evaluate

    tainted_values = _tainted_values(case)
    campaign_id = (case.campaign.match.campaign_id
                   if case.campaign and case.campaign.match else "")
    for draft in case.response.drafts:
        action = DRAFT_ACTIONS[draft.kind.value]
        d = evaluate(action, target=draft.intended_recipient, payload=draft.body,
                     approval_token=None, reported_indicators=tainted_values,
                     report_id=case.report.report_id, campaign_id=campaign_id)
        draft.requires_approval = not d.allowed


def _record_refused_requests(case: CaseFile) -> None:
    """Put what a hostile report asked for to the policy engine, and write down the answer.

    Nothing is attempted here. The report asked the system to fetch a URL, or to
    broadcast text of the attacker's choosing, or to mail the resident list
    somewhere; this asks the policy engine whether that would have been allowed
    and records the refusal against the report that requested it.

    Two reasons this is worth doing rather than simply not acting. First, "we
    never built that capability" is invisible — a coordinator cannot audit an
    absence, and neither can anyone reviewing the system. Second, it puts the
    request where it belongs: in the trail, tagged ``origin=untrusted-content``,
    next to the report id, so the refusal is attributable rather than ambient.
    """
    if not case.intake or not case.intake.contains_injection_attempt:
        return
    from .policy import evaluate

    text = f"{case.report.raw_content}\n{case.report.volunteer_note}"
    tainted_values = _tainted_values(case)
    for action in requested_capabilities(text):
        # Aim the request at the attacker's own infrastructure where the report
        # named some, because that is what the text actually asked for.
        target = tainted_values[0] if tainted_values and action in {"http.fetch", "voice.call"} \
            else "resident-list"
        evaluate(action, target=target, reported_indicators=tainted_values,
                 approval_token=None, report_id=case.report.report_id,
                 origin="untrusted-content", actor="report-content")
