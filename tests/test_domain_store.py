"""The persistence layer's load-bearing guarantees.

These are the properties the rest of the system assumes and would fail silently
without: dedup is scoped to one reporter, duplicates do not inflate correlation,
an approval is bound to the exact message, a capability cannot be spent twice,
and a job cannot be claimed twice.
"""
from datetime import timedelta

import pytest

from porchlight import db
from porchlight.domain import (
    Approval,
    AuditEvent,
    Campaign,
    CampaignEvidence,
    Case,
    CaseState,
    Decision,
    DecisionState,
    Indicator,
    IndicatorKind,
    JobState,
    OutboxMessage,
    ReportState,
    message_digest,
    utcnow,
)
from porchlight.models import Channel, Report

COMMUNITY = "test-coalition"


def make_report(rid: str, *, reporter: str = "resident-001", content: str = "parcel seized",
                area: str = "411038", days_ago: int = 0) -> Report:
    return Report(
        report_id=rid, community_id=COMMUNITY,
        received_at=utcnow() - timedelta(days=days_ago),
        channel=Channel.SMS, raw_content=content, reporter_pseudonym=reporter,
        reporter_area=area,
    )


# --------------------------------------------------------------------------
# Reports and dedup
# --------------------------------------------------------------------------
def test_inserting_the_same_report_id_twice_is_a_no_op():
    assert db.insert_report(make_report("r1"), "hash-a") is True
    assert db.insert_report(make_report("r1"), "hash-a") is False
    assert db.count_reports(COMMUNITY) == 1


def test_dedup_is_scoped_to_one_reporter():
    """Two residents describing the same call is the signal, not a duplicate.

    If dedup keyed on content alone it would delete exactly the evidence this
    product exists to find, and it would do it silently.
    """
    db.insert_report(make_report("r1", reporter="resident-001"), "same-hash")
    assert db.find_duplicate(COMMUNITY, "resident-001", "same-hash") == "r1"
    assert db.find_duplicate(COMMUNITY, "resident-002", "same-hash") is None


def test_a_duplicate_report_never_reaches_correlation():
    """A resident calling back three times must not read as three residents."""
    db.insert_report(make_report("r1", reporter="resident-001"), "h")
    db.insert_report(make_report("r2", reporter="resident-001"), "h",
                     state=ReportState.DUPLICATE, duplicate_of="r1")
    db.put_indicators([
        Indicator(report_id="r1", kind=IndicatorKind.PHONE, value="+91 90000 00042",
                  normalised="9000000042"),
        Indicator(report_id="r2", kind=IndicatorKind.PHONE, value="+91 90000 00042",
                  normalised="9000000042"),
    ])
    records = db.correlation_records(COMMUNITY, days=14)
    assert [r["report_id"] for r in records] == ["r1"]


def test_correlation_records_carry_their_indicator_keys():
    db.insert_report(make_report("r1"), "h")
    db.put_indicators([
        Indicator(report_id="r1", kind=IndicatorKind.PHONE, value="+91 90000 00042",
                  normalised="9000000042"),
        Indicator(report_id="r1", kind=IndicatorKind.UPI, value="payee7@ybl",
                  normalised="payee7@ybl"),
    ])
    rec = db.correlation_records(COMMUNITY, days=14)[0]
    assert rec["indicator_keys"] == ["phone:9000000042", "upi:payee7@ybl"]


def test_reports_outside_the_window_are_excluded():
    db.insert_report(make_report("old", days_ago=40), "h")
    db.insert_report(make_report("new", days_ago=1), "h2")
    ids = [r["report_id"] for r in db.correlation_records(COMMUNITY, days=14)]
    assert ids == ["new"]


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------
def test_a_case_owns_its_reports_and_survives_a_round_trip():
    db.insert_report(make_report("r1"), "h1")
    db.insert_report(make_report("r2"), "h2")
    case = Case(case_id="c1", community_id=COMMUNITY, urgency="red",
                script_fingerprint="parcel seized customs bribe",
                reporter_pseudonym="resident-001", report_ids=["r1", "r2"])
    db.upsert_case(case)

    loaded = db.get_case("c1")
    assert loaded is not None
    assert set(loaded.report_ids) == {"r1", "r2"}
    assert loaded.report_count == 2
    assert loaded.urgency == "red"


def test_upsert_updates_rather_than_duplicating():
    db.insert_report(make_report("r1"), "h1")
    case = Case(case_id="c1", community_id=COMMUNITY, report_ids=["r1"])
    db.upsert_case(case)
    case.urgency = "black"
    case.state = CaseState.ESCALATED
    db.upsert_case(case)
    assert len(db.list_cases(COMMUNITY)) == 1
    assert db.get_case("c1").urgency == "black"


def test_merge_target_is_found_only_for_the_same_resident_and_script():
    db.insert_report(make_report("r1"), "h1")
    db.upsert_case(Case(case_id="c1", community_id=COMMUNITY,
                        reporter_pseudonym="resident-001",
                        script_fingerprint="parcel seized customs bribe", report_ids=["r1"]))

    assert db.find_case_for_merge(COMMUNITY, "resident-001", "parcel seized customs bribe") == "c1"
    assert db.find_case_for_merge(COMMUNITY, "resident-002", "parcel seized customs bribe") is None
    assert db.find_case_for_merge(COMMUNITY, "resident-001", "kyc update account block") is None


def test_an_unclassified_script_is_never_a_merge_key():
    """Two reports we failed to classify are not thereby the same incident."""
    assert db.find_case_for_merge(COMMUNITY, "resident-001", "unclassified script") is None
    assert db.find_case_for_merge(COMMUNITY, "resident-001", "") is None


# --------------------------------------------------------------------------
# Campaigns
# --------------------------------------------------------------------------
def test_a_campaign_reports_whether_this_call_first_escalated_it():
    camp = Campaign(campaign_id="cmp_1", community_id=COMMUNITY, label="Parcel crew",
                    report_ids=["r1", "r2", "r3"],
                    evidence=CampaignEvidence(shared_indicators=["phone:9000000042"],
                                              distinct_reporters=3))
    assert db.upsert_campaign(camp) is True, "first write is the escalation"
    camp.report_ids.append("r4")
    assert db.upsert_campaign(camp) is False, "growing a known campaign is not a new escalation"
    assert db.get_campaign("cmp_1").report_ids == ["r1", "r2", "r3", "r4"]


def test_campaign_evidence_round_trips():
    ev = CampaignEvidence(shared_indicators=["phone:9000000042", "upi:payee7@ybl"],
                          distinct_reporters=3, areas=["411038"], span_days=4,
                          links=[{"a": "r1", "b": "r2", "via": "phone:9000000042"}])
    db.upsert_campaign(Campaign(campaign_id="cmp_1", community_id=COMMUNITY, evidence=ev,
                                report_ids=["r1", "r2", "r3"]))
    loaded = db.get_campaign("cmp_1")
    assert loaded.evidence.shared_indicators == ["phone:9000000042", "upi:payee7@ybl"]
    assert loaded.evidence.links[0]["via"] == "phone:9000000042"


# --------------------------------------------------------------------------
# Decisions — notification dedup
# --------------------------------------------------------------------------
def _decision(did: str, campaign_id: str = "cmp_1", kind: str = "community_sms") -> Decision:
    return Decision(decision_id=did, community_id=COMMUNITY, kind=kind, action="sms.send",
                    title="Warning", body="Neighbours are being called by a crew.",
                    audience="coalition resident list", campaign_id=campaign_id)


def test_a_second_identical_pending_decision_is_refused():
    """Re-running correlation must not put the same ask in the inbox twice."""
    assert db.create_decision(_decision("d1")) is True
    assert db.create_decision(_decision("d2")) is False
    assert len(db.list_decisions(COMMUNITY, DecisionState.PENDING)) == 1


def test_resolving_a_decision_frees_the_slot():
    db.create_decision(_decision("d1"))
    db.resolve_decision("d1", DecisionState.REJECTED, by="Priya Nair")
    assert db.create_decision(_decision("d2")) is True
    assert db.get_decision("d1").state is DecisionState.REJECTED
    assert db.get_decision("d1").resolved_by == "Priya Nair"


def test_different_kinds_are_different_decisions():
    assert db.create_decision(_decision("d1", kind="community_sms")) is True
    assert db.create_decision(_decision("d2", kind="community_flyer")) is True


# --------------------------------------------------------------------------
# Approvals — the capability model
# --------------------------------------------------------------------------
def _approval(body: str, **over) -> Approval:
    fields = dict(token="tok-1", approver="Priya Nair", role="coalition_coordinator",
                  action="sms.send", audience="resident-list",
                  message_hash=message_digest(body),
                  expires_at=utcnow() + timedelta(minutes=30))
    fields.update(over)
    return Approval(**fields)


def test_an_approval_matches_only_the_message_it_approved():
    body = "Neighbours: a crew is calling about a seized parcel. Hang up and call us."
    approval = _approval(body)
    ok, _ = approval.matches("sms.send", "resident-list", body)
    assert ok

    edited = body + " Call 555-0100 now."
    ok, reason = approval.matches("sms.send", "resident-list", edited)
    assert not ok and "changed after approval" in reason


def test_trailing_whitespace_does_not_invalidate_an_approval():
    """Re-approving for a stray newline would train people to click through."""
    body = "Neighbours: a crew is calling about a seized parcel."
    approval = _approval(body)
    ok, _ = approval.matches("sms.send", "resident-list", body + "\n  ")
    assert ok


def test_an_approval_does_not_transfer_to_another_action_or_audience():
    body = "text"
    approval = _approval(body)
    assert not approval.matches("flyer.publish", "resident-list", body)[0]
    assert not approval.matches("sms.send", "partner-agencies", body)[0]


def test_an_expired_approval_matches_nothing():
    body = "text"
    approval = _approval(body, expires_at=utcnow() - timedelta(seconds=1))
    ok, reason = approval.matches("sms.send", "resident-list", body)
    assert not ok and "expired" in reason


def test_a_capability_can_only_be_spent_once():
    body = "text"
    db.put_approval(_approval(body))
    assert db.spend_approval_atomic("tok-1") is True
    assert db.spend_approval_atomic("tok-1") is False, "replaying an approval must fail"
    assert db.get_approval("tok-1").spent is True


def test_an_unknown_token_cannot_be_spent():
    assert db.spend_approval_atomic("coord-override-999") is False
    assert db.get_approval("coord-override-999") is None


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------
def test_audit_filters_the_whole_trail_not_just_the_last_page():
    db.append_audit(AuditEvent(effect="forbid", policy_id="P001", action="http.fetch",
                               report_id="r1", origin="untrusted-content"))
    for i in range(100):
        db.append_audit(AuditEvent(effect="permit", policy_id="P000", action=f"store.write.{i}"))

    denials = db.list_audit(limit=10, effect="forbid")
    assert len(denials) == 1, "the denial must survive a flood of permits"
    assert denials[0]["policy_id"] == "P001"
    assert db.count_audit("forbid") == 1
    assert db.count_audit() == 101


def test_audit_is_newest_first():
    db.append_audit(AuditEvent(action="first"))
    db.append_audit(AuditEvent(action="second"))
    assert [e["action"] for e in db.list_audit(limit=2)] == ["second", "first"]


# --------------------------------------------------------------------------
# Sandbox outbox
# --------------------------------------------------------------------------
def test_delivery_lands_in_the_sandbox_outbox():
    db.append_outbox(OutboxMessage(decision_id="d1", channel="sms", audience="resident-list",
                                   body="hello", approver="Priya Nair"))
    sent = db.list_outbox()
    assert len(sent) == 1
    assert sent[0]["sandbox"] == 1
    assert sent[0]["approver"] == "Priya Nair"


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
def test_the_same_idempotency_key_enqueues_once():
    assert db.enqueue("ingest", {"report_id": "r1"}, "key-1") is True
    assert db.enqueue("ingest", {"report_id": "r1"}, "key-1") is False
    assert db.job_counts().get("pending") == 1


def test_a_job_is_claimed_exactly_once():
    db.enqueue("ingest", {"report_id": "r1"}, "key-1")
    first = db.claim_job()
    second = db.claim_job()
    assert first is not None and first.kind == "ingest"
    assert second is None, "a claimed job must not be handed to a second worker"


def test_jobs_retry_then_fail_visibly():
    db.enqueue("ingest", {"report_id": "r1"}, "key-1")
    for attempt in (1, 2):
        job = db.claim_job()
        assert job is not None
        assert db.retry_or_fail(job.job_id, attempt, "boom") is JobState.PENDING
    job = db.claim_job()
    assert db.retry_or_fail(job.job_id, 3, "boom") is JobState.FAILED

    assert db.claim_job() is None, "a failed job must not spin forever"
    failed = db.list_failed_jobs()
    assert len(failed) == 1 and "boom" in failed[0]["last_error"]


def test_state_survives_reopening_the_database(tmp_path):
    """The queue must outlive the process. This is why it is not a dict."""
    path = tmp_path / "persist.db"
    db.close()
    db.connect(path)
    db.insert_report(make_report("r1"), "h")
    db.upsert_case(Case(case_id="c1", community_id=COMMUNITY, report_ids=["r1"]))
    db.close()

    db.connect(path)
    assert db.get_case("c1") is not None
    assert db.count_reports(COMMUNITY) == 1
    db.close()


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own database file."""
    db.close()
    db.connect(tmp_path / "test.db")
    yield
    db.close()
