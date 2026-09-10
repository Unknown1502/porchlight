"""Background ingestion: the part that runs with nobody watching.

The guarantees asserted here are the ones a coordinator's trust rests on. If
replaying a batch double-counts, every "N residents affected" number is wrong. If
a failure disappears, the coordinator believes a report was handled when it was
not. Both are worse than the system being slow.
"""
from datetime import datetime, timedelta, timezone

import pytest

from porchlight import db, ingest
from porchlight.domain import CaseState, ReportState
from porchlight.models import Channel, Report

COMMUNITY = "test-coalition"

PARCEL = ("Customs officer here. Your parcel contains contraband. Pay Rs 85,000 to "
          "{handle} within 2 hours or a warrant is issued. Call {phone}.")


def _report(rid: str, *, reporter="resident-001", handle="payee042@ybl",
            phone="+91 90000 00042", content=None, days_ago=0, area="411038") -> Report:
    return Report(
        report_id=rid, community_id=COMMUNITY,
        received_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        channel=Channel.SMS,
        raw_content=content or PARCEL.format(handle=handle, phone=phone),
        reporter_pseudonym=reporter, reporter_area=area)


def _ingest(*reports: Report) -> dict:
    for r in reports:
        ingest.accept(r)
    return ingest.drain()


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def test_replaying_the_same_batch_processes_nothing_twice():
    """The single most important property of the ingest path."""
    batch = [_report(f"r{i}", reporter=f"resident-{i:03d}") for i in range(5)]
    first = _ingest(*batch)
    assert first["processed"] == 5

    # Same artefacts, arriving again under fresh report ids.
    again = [_report(f"again-{i}", reporter=f"resident-{i:03d}") for i in range(5)]
    second = _ingest(*again)

    assert second.get("processed", 0) == 0, "a replay must not create new cases"
    assert len(db.list_cases(COMMUNITY)) == 5


def test_the_same_report_id_submitted_twice_is_rejected_once():
    assert ingest.accept(_report("r1"))["accepted"] is True
    repeat = ingest.accept(_report("r1"))
    assert repeat["accepted"] is False
    assert db.count_reports(COMMUNITY) == 1


def test_identical_artefact_from_the_same_resident_is_folded_in():
    _ingest(_report("r1", reporter="resident-001"))
    result = ingest.accept(_report("r2", reporter="resident-001"))
    assert result["queued"] is False
    assert result["duplicate_of"] == "r1"
    assert db.get_report("r2")["state"] == ReportState.DUPLICATE.value


def test_two_residents_reporting_the_same_call_are_two_reports():
    """The signal, not a duplicate. Getting this wrong deletes the product."""
    _ingest(_report("r1", reporter="resident-001"),
            _report("r2", reporter="resident-002"))
    states = {db.get_report(r)["state"] for r in ("r1", "r2")}
    assert ReportState.DUPLICATE.value not in states
    assert len(db.list_cases(COMMUNITY)) == 2


# --------------------------------------------------------------------------
# Case merging
# --------------------------------------------------------------------------
def test_a_resident_calling_back_extends_their_case_rather_than_opening_another():
    _ingest(_report("r1", reporter="resident-001"))
    # Same resident, same script, more detail — a genuinely different artefact.
    follow_up = _report("r2", reporter="resident-001",
                        content=PARCEL.format(handle="payee042@ybl", phone="+91 90000 00042")
                        + " She has now been told to go to the bank in person.")
    _ingest(follow_up)

    cases = db.list_cases(COMMUNITY)
    assert len(cases) == 1, "a follow-up must not open a second case"
    assert set(cases[0].report_ids) == {"r1", "r2"}


def test_a_merge_never_lowers_the_urgency_already_recorded():
    """A vaguer follow-up must not downgrade a case that is already urgent."""
    _ingest(_report("r1", reporter="resident-001",
                    content=PARCEL.format(handle="payee042@ybl", phone="+91 90000 00042")
                    + " She has already transferred the money."))
    before = db.list_cases(COMMUNITY)[0].urgency
    assert before == "black"

    _ingest(_report("r2", reporter="resident-001",
                    content=PARCEL.format(handle="payee042@ybl", phone="+91 90000 00042")
                    + " Adding that the caller had an accent."))
    assert db.list_cases(COMMUNITY)[0].urgency == "black"


# --------------------------------------------------------------------------
# Campaign detection through the background path
# --------------------------------------------------------------------------
def test_a_campaign_forms_across_residents_with_nobody_watching():
    _ingest(*[_report(f"r{i}", reporter=f"resident-{i:03d}") for i in range(3)])

    campaigns = db.list_campaigns(COMMUNITY)
    assert len(campaigns) == 1
    camp = campaigns[0]
    assert camp.evidence.distinct_reporters == 3
    assert camp.evidence.shared_indicators, "a campaign must state what links it"
    assert camp.evidence.links, "and which report pairs it links"
    assert camp.escalated_by_report_id == "r2", "the third report is what tipped it"


def test_a_duplicate_does_not_inflate_a_campaign():
    """Three reports from two residents, one of them a repeat, is not a campaign."""
    _ingest(_report("r1", reporter="resident-001"),
            _report("r2", reporter="resident-002"),
            _report("r3", reporter="resident-001"))  # identical to r1
    assert db.list_campaigns(COMMUNITY) == []


def test_unrelated_crews_do_not_merge():
    _ingest(*[_report(f"a{i}", reporter=f"resident-a{i}", handle="payee001@ybl",
                      phone="+91 90000 00001") for i in range(3)],
            *[_report(f"b{i}", reporter=f"resident-b{i}", handle="payee999@ybl",
                      phone="+91 90000 00099", area="440010") for i in range(3)])
    campaigns = db.list_campaigns(COMMUNITY)
    assert len(campaigns) == 2, "two crews must stay two campaigns"
    for camp in campaigns:
        assert len(camp.report_ids) == 3


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------
def test_a_failing_report_is_retried_then_surfaced_not_dropped(monkeypatch):
    calls = {"n": 0}

    def explode(report_id):
        calls["n"] += 1
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(ingest, "process_one", explode)
    ingest.accept(_report("r-bad"))
    for _ in range(4):
        ingest.drain()

    assert calls["n"] == 3, "bounded retry, not infinite"
    assert db.get_report("r-bad")["state"] == ReportState.FAILED.value
    assert len(db.list_failed_jobs()) == 1
    denials = db.list_audit(limit=50, effect="forbid")
    assert any(e["report_id"] == "r-bad" for e in denials), "a failure must be visible"


def test_one_bad_report_does_not_stop_the_queue(monkeypatch):
    real = ingest.process_one

    def sometimes(report_id):
        if report_id == "r-bad":
            raise RuntimeError("boom")
        return real(report_id)

    monkeypatch.setattr(ingest, "process_one", sometimes)
    for r in (_report("r-good1", reporter="resident-001"),
              _report("r-bad", reporter="resident-002", handle="payee777@ybl"),
              _report("r-good2", reporter="resident-003", handle="payee888@ybl")):
        ingest.accept(r)
    for _ in range(4):
        ingest.drain()

    assert db.get_report("r-good1")["state"] == ReportState.PROCESSED.value
    assert db.get_report("r-good2")["state"] == ReportState.PROCESSED.value
    assert db.get_report("r-bad")["state"] == ReportState.FAILED.value


def test_an_unclassifiable_report_goes_to_review_not_to_green():
    """Never silently guess. A case nobody can classify needs a human."""
    _ingest(_report("r-vague", content="Someone rang. She hung up. Nothing else known."))
    row = db.get_report("r-vague")
    assert row["state"] == ReportState.NEEDS_REVIEW.value
    assert db.list_cases(COMMUNITY)[0].state is CaseState.AWAITING_HUMAN


# --------------------------------------------------------------------------
# The worker thread
# --------------------------------------------------------------------------
def test_the_worker_processes_reports_without_anyone_asking():
    worker = ingest.Worker(interval=0.05)
    worker.start()
    try:
        for i in range(3):
            ingest.accept(_report(f"r{i}", reporter=f"resident-{i:03d}"))
        assert ingest.wait_until_drained(timeout=15.0), "worker did not drain the queue"
    finally:
        worker.stop()

    assert db.count_reports(COMMUNITY, ReportState.PROCESSED) == 3
    assert len(db.list_campaigns(COMMUNITY)) == 1


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    db.close()
    db.connect(tmp_path / "ingest.db")
    from porchlight.tools.store import get_store
    get_store().clear(COMMUNITY)
    yield
    get_store().clear(COMMUNITY)
    db.close()
