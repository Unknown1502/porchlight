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
from .models import DRAFT_ACTIONS, CaseFile, IndicatorFinding, Report
from .policy import PolicyDenied, enforce, recent_audit
from .prompts import STAGE_TASK_TEMPLATE, envelope
from .tools.indicators import extract_indicators, indicator_keys, merge_indicators
from .tools.sanitize import requested_capabilities, scrub_pii
from .trace import TraceRecorder
from .tools.store import record_processed

log = logging.getLogger(__name__)


def _dump(obj: Any) -> str:
    return json.dumps(obj.model_dump(mode="json"), indent=2, default=str) if obj else "{}"


def process_report(report: Report, *, offline: bool | None = None,
                   recorder: TraceRecorder | None = None) -> CaseFile:
    """Run one report through the full pipeline."""
    s = settings()
    use_offline = s.offline if offline is None else offline
    case = CaseFile(report=report)
    t0 = time.perf_counter()
    rec = recorder or TraceRecorder(
        report.report_id, "offline-deterministic" if use_offline else "bedrock")

    # Defensive pre-processing. Applied before any model sees the artefact.
    report = report.model_copy(update={"raw_content": scrub_pii(report.raw_content)})
    case.report = report

    try:
        if use_offline:
            _run_offline(case, report, rec)
        else:
            _run_agents(case, report, rec)
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
    case.trace = rec.trace
    log.info("processed %s in %.2fs", report.report_id, time.perf_counter() - t0)
    return case


# --------------------------------------------------------------------------
def _missing_evidence(intake) -> list[str]:
    """What intake could not establish. Drives the corroboration step's tool choice.

    Named explicitly rather than left implicit because "what is missing" is the
    question that makes the corroboration agent's job legible to a coordinator —
    and because a gap nobody names is a gap nobody fills.
    """
    gaps = []
    if not intake.indicators.phone_numbers:
        gaps.append("no callback number")
    if not (intake.indicators.upi_ids or intake.indicators.crypto_addresses
            or intake.indicators.bank_accounts_masked):
        gaps.append("no beneficiary identifier")
    if intake.money_rail.value in {"none", "unknown"}:
        gaps.append("payment rail unclear")
    if not intake.impersonated_entity:
        gaps.append("impersonated body not named")
    if intake.amount_demanded is None:
        gaps.append("amount not stated")
    return gaps


def _run_offline(case: CaseFile, report: Report, rec: TraceRecorder) -> None:
    from . import offline
    from .tools.enrichment import check_payment_handle, check_url_reputation, phone_shape

    s = settings()

    with rec.step("intake") as st:
        case.intake = offline.offline_intake(report)
        st.evidence_used = sorted(indicator_keys(case.intake.indicators))
        st.missing_evidence = _missing_evidence(case.intake)
        st.outcome = (f"script='{case.intake.script_fingerprint}', "
                      f"rail={case.intake.money_rail.value}, "
                      f"{len(st.evidence_used)} hard indicator(s)")

    with rec.step("corroboration") as st:
        # Only chase what is actually missing, and only within the budget. A
        # report naming forty URLs must not be able to spend forty tool calls.
        for url in case.intake.indicators.urls[:3]:
            if rec.budget_exhausted(s.max_tool_calls_per_report):
                break
            rec.record_tool(st, "check_url_reputation", url,
                            check_url_reputation(url))
        for handle in (case.intake.indicators.upi_ids
                       + case.intake.indicators.crypto_addresses)[:2]:
            if rec.budget_exhausted(s.max_tool_calls_per_report):
                break
            rec.record_tool(st, "check_payment_handle", handle,
                            check_payment_handle(handle))
        for number in case.intake.indicators.phone_numbers[:2]:
            if rec.budget_exhausted(s.max_tool_calls_per_report):
                break
            rec.record_tool(st, "phone_shape", number,
                            phone_shape(number))

        case.corroboration = offline.offline_corroboration(report, case.intake)
        case.corroboration.findings.extend(
            IndicatorFinding(indicator=c.argument, kind=_finding_kind(c.tool),
                             source=c.source or "tool", verdict=c.verdict or "unknown",
                             detail=c.detail)
            for c in st.tools_called if c.verdict
        )
        malicious = sum(1 for c in st.tools_called if c.verdict == "malicious")
        st.evidence_used = list(case.corroboration.prior_reports_matched)
        st.outcome = (f"{len(st.tools_called)} tool call(s), {malicious} malicious verdict(s), "
                      f"{len(case.corroboration.prior_reports_matched)} prior report(s) matched; "
                      f"external confidence={case.corroboration.external_confidence}")

    with rec.step("stage") as st:
        case.stage = offline.offline_stage(report, case.intake)
        st.outcome = (f"{case.stage.urgency.value.upper()} — {case.stage.playbook_stage}")
        st.recommendation = case.stage.recommended_human_action
        st.evidence_used = list(case.stage.isolation_signals)

    with rec.step("correlation") as st:
        # Correlation must see THIS report, so persist a provisional record first.
        _provisional_record(case, report)
        case.campaign = offline.offline_campaign(report, case.intake)
        if case.campaign.is_campaign and case.campaign.match:
            m = case.campaign.match
            st.evidence_used = list(m.shared_indicators)
            st.outcome = (f"campaign {m.campaign_id}: {len(m.member_report_ids)} reports, "
                          f"{m.distinct_reporters} residents, confidence {m.confidence}"
                          + (" [NEWLY ESCALATED]" if case.campaign.newly_escalated else ""))
        else:
            st.outcome = "no cluster cleared the volume, breadth and evidence thresholds"

    with rec.step("response") as st:
        case.response = offline.offline_response(report, case.intake, case.stage, case.campaign)
        _check_drafts(case)
        _record_refused_requests(case)
        gated = [d.kind.value for d in case.response.drafts if d.requires_approval]
        st.outcome = (f"{len(case.response.drafts)} draft(s); "
                      f"{len(gated)} held at the policy boundary: {', '.join(gated) or 'none'}")
        st.recommendation = case.response.decision_for_human


def _finding_kind(tool_name: str) -> str:
    return {"check_url_reputation": "url", "check_payment_handle": "upi",
            "phone_shape": "phone", "domain_age_days": "domain"}.get(tool_name, "other")


def _run_agents(case: CaseFile, report: Report, rec: TraceRecorder) -> None:
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
    with rec.step("intake") as st:
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
        st.evidence_used = sorted(indicator_keys(intake.indicators))
        st.missing_evidence = _missing_evidence(intake)
        st.outcome = (f"script='{intake.script_fingerprint}', rail={intake.money_rail.value}, "
                      f"{len(st.evidence_used)} hard indicator(s)")

    # --- Node 2: corroboration -----------------------------------------
    from .models import CorroborationResult

    with rec.step("corroboration") as st:
        keys = sorted(indicator_keys(intake.indicators))
        case.corroboration = _tool_then_structure(
            build_corroboration_agent(),
            CorroborationResult,
            "Corroborate this report using your tools. Check only these indicators.\n\n"
            f"script_fingerprint: {intake.script_fingerprint}\n"
            f"indicators: {json.dumps(keys)}\n\n"
            f"Structured intake (trusted):\n{_dump(intake)}",
        )
        # The agent chose which tools to call; the trace records what came back,
        # so the choice is reviewable without exposing how it was reasoned about.
        for finding in case.corroboration.findings:
            rec.record_tool(st, f"{finding.kind}_check", finding.indicator, {
                "verdict": finding.verdict, "detail": finding.detail,
                "source": finding.source, "fixture_backed": finding.source == "fixture",
            })
        st.evidence_used = list(case.corroboration.prior_reports_matched)
        st.outcome = (f"{len(case.corroboration.findings)} finding(s); "
                      f"external confidence={case.corroboration.external_confidence}")

    # --- Node 3: stage swarm -------------------------------------------
    from .models import StageAssessment

    with rec.step("stage") as st:
        swarm_task = STAGE_TASK_TEMPLATE.format(
            intake_json=_dump(intake),
            corroboration_json=_dump(case.corroboration),
            volunteer_note=report.volunteer_note or "(none)",
            artefact_envelope=art,
        )
        swarm_result = build_stage_swarm()(swarm_task)
        case.stage = _extract_structured(swarm_result, StageAssessment)
        st.outcome = f"{case.stage.urgency.value.upper()} — {case.stage.playbook_stage}"
        st.recommendation = case.stage.recommended_human_action
        st.evidence_used = list(case.stage.isolation_signals)

    # --- Node 4: campaign ----------------------------------------------
    from .models import CampaignResult

    with rec.step("correlation") as st:
        _provisional_record(case, report)
        case.campaign = _tool_then_structure(
            build_campaign_agent(),
            CampaignResult,
            f"Judge whether report {report.report_id} belongs to a real campaign. "
            f"Call find_candidate_cluster('{report.report_id}') first.\n\n"
            f"This report's intake:\n{_dump(intake)}",
        )
        if case.campaign.is_campaign and case.campaign.match:
            m = case.campaign.match
            st.evidence_used = list(m.shared_indicators)
            st.outcome = (f"campaign {m.campaign_id}: {len(m.member_report_ids)} reports, "
                          f"{m.distinct_reporters} residents, confidence {m.confidence}"
                          + (" [NEWLY ESCALATED]" if case.campaign.newly_escalated else ""))
        else:
            st.outcome = "the agent judged no candidate cluster to be one crew"

    # --- Node 5: response ----------------------------------------------
    from .models import ResponseResult

    with rec.step("response") as st:
        case.response = build_response_agent().structured_output(
            ResponseResult,
            "Draft what this case needs. Draft nothing it does not need.\n\n"
            f"Jurisdiction pack: {json.dumps({'name': pack.display_name, 'reporting': pack.reporting, 'partners': pack.partners}, default=str)}\n\n"
            f"Intake:\n{_dump(intake)}\n\nStage:\n{_dump(case.stage)}\n\nCampaign:\n{_dump(case.campaign)}",
        )
        _check_drafts(case)
        _record_refused_requests(case)
        gated = [d.kind.value for d in case.response.drafts if d.requires_approval]
        st.outcome = (f"{len(case.response.drafts)} draft(s); "
                      f"{len(gated)} held at the policy boundary: {', '.join(gated) or 'none'}")
        st.recommendation = case.response.decision_for_human


def _tool_then_structure(agent: Any, model_cls: Any, task: str):
    """Let a tool-bearing agent use its tools, then convert the result to a schema.

    ``Agent.structured_output(Model, prompt)`` is a single request that expects the
    model's tool-use turn to *be* the structured output. That works for an agent
    with no tools. For an agent that has real tools, the model spends its tool-use
    turn calling them, and the call fails with:

        ValueError: No valid tool use or tool use input was found in the Bedrock
        response.

    which is what live mode did at the corroboration node — after the tools had
    run correctly. Offline mode never exercises this path, so the whole live
    pipeline was broken behind a green test suite.

    Two phases fixes it: converse first so the tools actually run, then ask for
    the schema over the finished conversation. The second call carries no prompt,
    so the model is summarising work it already did rather than starting again.
    """
    agent(task)
    return agent.structured_output(model_cls)


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

    Deliberately **not** gated on ``contains_injection_attempt``. That flag comes
    from a signature list whose measured recall on held-out evasions is about
    0.29, so gating on it meant a report that plainly asked us to fetch its link
    was never put to the policy engine at all — the weakest component in the
    system was deciding whether the strongest one got consulted. Asking what the
    text requested, and asking whether that is permitted, are independent
    questions and are now answered independently.
    """
    if not case.intake:
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
