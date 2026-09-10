"""Ingestion and the background worker.

This is the part that makes Porchlight a background agent rather than a form the
coordinator drives. Reports arrive while nobody is watching — from the webhook,
from a replay, eventually from a partner system — and are processed to a finished
case without anyone opening the dashboard.

Three properties matter more than throughput:

**Idempotency.** Replaying a batch must not duplicate anything. The idempotency
key is a hash of (community, reporter, artefact), so the same submission collapses
to one job however many times it arrives.

**Nothing is silently dropped or silently guessed.** A report that fails
processing is retried a bounded number of times and then marked `failed` and
surfaced in the UI. A report the pipeline could not classify is marked
`needs_review`, not quietly filed as green.

**Duplicate suppression is not deduplication of people.** Two residents
describing the same call are two reports and the whole point of the product. One
resident calling back three times is one case. These are different questions and
the code answers them separately — see `_classify`.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any, Optional

from . import db
from .config import settings
from .domain import (
    AuditEvent,
    Campaign,
    CampaignEvidence,
    Case,
    CaseState,
    Indicator,
    IndicatorKind,
    JobState,
    ReportState,
    utcnow,
)
from .models import CaseFile, Report
from .pipeline import process_report
from .tools.indicators import indicator_keys

log = logging.getLogger(__name__)

JOB_INGEST = "ingest_report"


# --------------------------------------------------------------------------
# Accepting a report
# --------------------------------------------------------------------------
def content_hash(report: Report) -> str:
    """Identity of the *substance* of a report.

    Whitespace-normalised and case-folded so that the same message pasted twice,
    or re-typed with different spacing, is recognised as the same artefact. The
    volunteer's note is excluded on purpose: a coordinator adding context to an
    existing report has not created a new incident.
    """
    body = " ".join(report.raw_content.split()).casefold()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def idempotency_key(report: Report) -> str:
    """Same artefact, same reporter, same community => the same job."""
    basis = f"{report.community_id}|{report.reporter_pseudonym}|{content_hash(report)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def accept(report: Report) -> dict[str, Any]:
    """Take a report in and queue it. Returns what happened, for the caller.

    Deliberately cheap: this runs inside the webhook request, so it does no model
    work. Everything expensive happens in the worker.

    The submitted ``community_id`` is **not** trusted. This deployment serves one
    coalition, named in configuration, and a field in an inbound payload must not
    be able to steer a report into another community's data — nor quietly out of
    this one's, which is how a report gets accepted with a 202 and then never
    appears in anybody's queue.
    """
    configured = settings().community_id
    if report.community_id != configured:
        log.info("coercing report %s from community %r to the configured %r",
                 report.report_id, report.community_id, configured)
        report = report.model_copy(update={"community_id": configured})

    key = idempotency_key(report)
    inserted = db.insert_report(report, content_hash(report), state=ReportState.QUEUED)
    if not inserted:
        return {"accepted": False, "reason": "report_id already present",
                "report_id": report.report_id}

    queued = db.enqueue(JOB_INGEST, {"report_id": report.report_id}, key)
    if not queued:
        # The artefact is already in flight or done under a different report id.
        # Record it as a duplicate rather than dropping it: the coordinator
        # should still be able to see that it arrived again.
        prior = db.find_duplicate(report.community_id, report.reporter_pseudonym,
                                  content_hash(report))
        db.update_report(report.report_id, state=ReportState.DUPLICATE,
                         duplicate_of=prior or "")
        return {"accepted": True, "queued": False, "duplicate_of": prior,
                "report_id": report.report_id, "reason": "identical artefact already ingested"}

    return {"accepted": True, "queued": True, "report_id": report.report_id}


# --------------------------------------------------------------------------
# Processing one report
# --------------------------------------------------------------------------
def _classify(report: Report) -> tuple[ReportState, str]:
    """Is this a fresh incident, or the same resident telling us again?"""
    prior = db.find_duplicate(report.community_id, report.reporter_pseudonym,
                              content_hash(report), within_days=settings().campaign_window_days)
    if prior and prior != report.report_id:
        return ReportState.DUPLICATE, prior
    return ReportState.PROCESSED, ""


def _store_indicators(report_id: str, case_file: CaseFile) -> None:
    if not case_file.intake:
        return
    ind = case_file.intake.indicators
    rows: list[Indicator] = []
    raw_by_norm: dict[str, str] = {}
    for value in ind.phone_numbers:
        raw_by_norm[value] = value
    for key in sorted(indicator_keys(ind)):
        kind, _, normalised = key.partition(":")
        try:
            kind_enum = IndicatorKind(kind)
        except ValueError:
            continue
        rows.append(Indicator(report_id=report_id, kind=kind_enum,
                              value=raw_by_norm.get(normalised, normalised),
                              normalised=normalised))
    db.put_indicators(rows)


def _needs_review(case_file: CaseFile) -> bool:
    """Route the genuinely ambiguous to a human instead of guessing.

    Two triggers: the pipeline errored, or intake could not classify the script
    *and* found no hard indicator — which means correlation has nothing to work
    with and a confident-looking green band would be a fabrication.
    """
    if case_file.errors:
        return True
    intake = case_file.intake
    if intake is None:
        return True
    unclassified = intake.script_fingerprint in {"", "unclassified script"}
    return unclassified and not indicator_keys(intake.indicators)


def process_one(report_id: str) -> dict[str, Any]:
    """Run one queued report all the way to a stored case."""
    row = db.get_report(report_id)
    if row is None:
        return {"report_id": report_id, "status": "unknown_report"}

    report = Report(
        report_id=row["report_id"], community_id=row["community_id"],
        received_at=row["received_at"], channel=row["channel"],
        raw_content=row["raw_content"], volunteer_note=row["volunteer_note"],
        reporter_pseudonym=row["reporter_pseudonym"], reporter_area=row["reporter_area"],
    )

    state, prior = _classify(report)
    if state is ReportState.DUPLICATE:
        db.update_report(report_id, state=ReportState.DUPLICATE, duplicate_of=prior,
                         case_id=(db.get_report(prior) or {}).get("case_id"))
        db.append_audit(AuditEvent(
            action="report.dedup", effect="permit", policy_id="P000", origin="system",
            actor="porchlight-agent", report_id=report_id,
            reason=f"same reporter and artefact as {prior}; folded in rather than counted again",
        ))
        return {"report_id": report_id, "status": "duplicate", "duplicate_of": prior}

    case_file = process_report(report)
    _store_indicators(report_id, case_file)

    final_state = ReportState.NEEDS_REVIEW if _needs_review(case_file) else ReportState.PROCESSED
    urgency = case_file.stage.urgency.value if case_file.stage else "green"
    fingerprint = case_file.intake.script_fingerprint if case_file.intake else ""
    db.update_report(report_id, state=final_state, script_fingerprint=fingerprint,
                     urgency=urgency)

    case = _attach_to_case(report, case_file, final_state)
    campaign_id = _record_campaign(report, case_file, case)
    return {"report_id": report_id, "status": final_state.value, "case_id": case.case_id,
            "campaign_id": campaign_id, "urgency": urgency,
            "newly_escalated": bool(case_file.campaign and case_file.campaign.newly_escalated)}


def _attach_to_case(report: Report, case_file: CaseFile, state: ReportState) -> Case:
    """Extend this resident's open case about the same script, or open a new one."""
    fingerprint = case_file.intake.script_fingerprint if case_file.intake else ""
    existing_id = db.find_case_for_merge(report.community_id, report.reporter_pseudonym,
                                         fingerprint)
    stage = case_file.stage
    if state is ReportState.NEEDS_REVIEW:
        # Ambiguous input gets a state a human will look at, not a confident
        # green band. Guessing here is how a real incident gets filed as noise.
        case_state = CaseState.AWAITING_HUMAN
    elif case_file.campaign and case_file.campaign.is_campaign:
        case_state = CaseState.ESCALATED
    elif stage and stage.urgency.value == "green":
        case_state = CaseState.WATCHING
    else:
        case_state = CaseState.OPEN

    if existing_id:
        case = db.get_case(existing_id)
        assert case is not None
        case.report_ids = sorted(set(case.report_ids) | {report.report_id})
        case.updated_at = utcnow()
        # An update only ever raises urgency. A follow-up call that mentions less
        # than the first must not downgrade a case that is already RED.
        case.urgency = _max_urgency(case.urgency, stage.urgency.value if stage else "green")
        if stage and stage.hours_to_irreversible is not None:
            case.hours_to_irreversible = (
                stage.hours_to_irreversible if case.hours_to_irreversible is None
                else min(case.hours_to_irreversible, stage.hours_to_irreversible))
        case.state = case_state
        db.upsert_case(case)
        db.append_audit(AuditEvent(
            action="case.merge", effect="permit", policy_id="P000", origin="system",
            report_id=report.report_id, case_id=case.case_id,
            reason=f"same resident, same script; merged into existing case {case.case_id}",
        ))
        return case

    case = Case(
        case_id=f"case-{report.report_id}", community_id=report.community_id,
        state=case_state, urgency=stage.urgency.value if stage else "green",
        hours_to_irreversible=stage.hours_to_irreversible if stage else None,
        script_fingerprint=fingerprint,
        impersonated_entity=case_file.intake.impersonated_entity if case_file.intake else "",
        money_rail=case_file.intake.money_rail.value if case_file.intake else "none",
        reporter_pseudonym=report.reporter_pseudonym, reporter_area=report.reporter_area,
        summary=(case_file.intake.pretext if case_file.intake else "")[:200],
        report_ids=[report.report_id],
    )
    db.upsert_case(case)
    return case


_URGENCY_ORDER = {"green": 0, "amber": 1, "red": 2, "black": 3}


def _max_urgency(a: str, b: str) -> str:
    return a if _URGENCY_ORDER.get(a, 0) >= _URGENCY_ORDER.get(b, 0) else b


def _record_campaign(report: Report, case_file: CaseFile, case: Case) -> str:
    """Persist the campaign this report joined, if any."""
    if not (case_file.campaign and case_file.campaign.is_campaign and case_file.campaign.match):
        return ""
    m = case_file.campaign.match
    campaign = Campaign(
        campaign_id=m.campaign_id, community_id=report.community_id, label=m.campaign_label,
        first_seen=m.first_seen, last_seen=m.last_seen, confidence=m.confidence,
        report_ids=list(m.member_report_ids),
        evidence=CampaignEvidence(
            shared_indicators=list(m.shared_indicators),
            script_fingerprints=[m.shared_script_fingerprint] if m.shared_script_fingerprint else [],
            distinct_reporters=m.distinct_reporters, areas=list(m.areas_affected),
            span_days=max((m.last_seen - m.first_seen).days, 1),
            links=_evidence_links(m.member_report_ids, m.shared_indicators),
        ),
        escalated_by_report_id=report.report_id if case_file.campaign.newly_escalated else "",
    )
    first_time = db.upsert_campaign(campaign)
    case.campaign_id = m.campaign_id
    case.state = CaseState.ESCALATED
    db.upsert_case(case)

    if first_time:
        db.append_audit(AuditEvent(
            action="campaign.escalate", effect="permit", policy_id="P000", origin="system",
            report_id=report.report_id, case_id=case.case_id, campaign_id=m.campaign_id,
            reason=(f"{len(m.member_report_ids)} reports from {m.distinct_reporters} residents "
                    f"share {', '.join(m.shared_indicators) or 'one script'}"),
        ))
        _raise_warning_decision(report, case_file, case, m)
    return m.campaign_id


def _raise_warning_decision(report: Report, case_file: CaseFile, case: Case, match: Any) -> None:
    """Prepare the one thing the coordinator has to decide.

    This is the whole interaction model: the agent has done the work, and what
    surfaces is a single prepared action with its evidence attached. Raised only
    when a campaign *first* clears threshold — a campaign that merely grew is not
    a new decision, and re-asking would teach the coordinator to skim.
    """
    from .approvals import raise_decision

    draft = next((d for d in (case_file.response.drafts if case_file.response else [])
                  if d.kind.value == "community_sms"), None)
    if draft is None:
        return

    raise_decision(
        community_id=report.community_id,
        kind="community_sms",
        action="sms.send",
        title=f"Warn the {', '.join(match.areas_affected) or 'coalition'} list — {match.campaign_label}",
        body=draft.body,
        audience=draft.intended_recipient,
        rationale=(f"{len(match.member_report_ids)} reports from {match.distinct_reporters} "
                   f"residents in {max((match.last_seen - match.first_seen).days, 1)} day(s) "
                   f"share {', '.join(match.shared_indicators) or 'the same script'}. "
                   f"This crew has not finished working the area."),
        case_id=case.case_id,
        campaign_id=match.campaign_id,
        evidence={
            "shared_indicators": list(match.shared_indicators),
            "script_fingerprint": match.shared_script_fingerprint,
            "member_report_ids": list(match.member_report_ids),
            "distinct_reporters": match.distinct_reporters,
            "areas": list(match.areas_affected),
            "confidence": match.confidence,
            "why": match.why,
        },
    )


def _evidence_links(report_ids: list[str], shared: list[str]) -> list[dict[str, str]]:
    """Per-pair provenance, so the UI can answer 'why is this report in here'."""
    links: list[dict[str, str]] = []
    for via in shared[:4]:
        for i in range(len(report_ids) - 1):
            links.append({"a": report_ids[i], "b": report_ids[i + 1], "via": via})
    return links[:24]


# --------------------------------------------------------------------------
# The worker
# --------------------------------------------------------------------------
def drain(max_jobs: int = 1000) -> dict[str, int]:
    """Process pending jobs until there are none. Returns a tally.

    Used by the replay path and by tests; the background thread calls it on a
    timer. Synchronous and re-entrant-safe because claiming is atomic.
    """
    tally = {"processed": 0, "duplicate": 0, "needs_review": 0, "failed": 0}
    for _ in range(max_jobs):
        job = db.claim_job()
        if job is None:
            break
        report_id = str(job.payload.get("report_id", ""))
        try:
            result = process_one(report_id)
            db.finish_job(job.job_id, JobState.DONE)
            status = result.get("status", "processed")
            tally[status] = tally.get(status, 0) + 1
        except Exception as exc:  # noqa: BLE001 — one bad report must not stop the queue
            log.exception("job %s failed on report %s", job.job_id, report_id)
            state = db.retry_or_fail(job.job_id, job.attempts, f"{type(exc).__name__}: {exc}")
            if state is JobState.FAILED:
                db.update_report(report_id, state=ReportState.FAILED)
                db.append_audit(AuditEvent(
                    action="report.process", effect="forbid", policy_id="P000", origin="system",
                    report_id=report_id, reason=f"processing failed after retries: {exc}",
                ))
                tally["failed"] += 1
    return tally


class Worker:
    """Background thread that drains the queue on an interval.

    Started by the server so reports are processed with nobody watching. Kept
    deliberately simple — one thread, one loop — because the correctness that
    matters (atomic claim, bounded retry, idempotent enqueue) lives in the
    database, not in the scheduler.
    """

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_tally: dict[str, int] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="porchlight-worker", daemon=True)
        self._thread.start()
        log.info("background worker started (interval=%.1fs)", self.interval)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                tally = drain()
                if any(tally.values()):
                    self.last_tally = tally
                    log.info("worker drained: %s", tally)
            except Exception:  # noqa: BLE001 — the worker must not die on one bad batch
                log.exception("worker iteration failed")
            self._stop.wait(self.interval)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


_WORKER: Optional[Worker] = None


def worker() -> Worker:
    global _WORKER
    if _WORKER is None:
        _WORKER = Worker()
    return _WORKER


def wait_until_drained(timeout: float = 30.0, poll: float = 0.05) -> bool:
    """Block until the queue is empty. For tests and the replay endpoint."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        counts = db.job_counts()
        if not counts.get("pending") and not counts.get("running"):
            return True
        time.sleep(poll)
    return False
