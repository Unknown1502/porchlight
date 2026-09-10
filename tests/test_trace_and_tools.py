"""Operational traces and fixture-backed corroboration.

The trace is what a coordinator is shown about the agent's work, so two things
are asserted: that it contains the operational facts, and that it does *not*
contain a free-text reasoning field a model could fill with whatever a hostile
report pushed into it.
"""
from datetime import datetime, timezone

from porchlight.models import Channel, Report
from porchlight.pipeline import process_report
from porchlight.tools import fixtures
from porchlight.tools.enrichment import (
    check_payment_handle,
    check_url_reputation,
    domain_age_days,
    phone_shape,
)
from porchlight.trace import Trace, TraceRecorder

RICH = ("Customs officer here. Your parcel contains contraband. "
        "Pay Rs 85,000 to payee042@ybl within 2 hours or a warrant is issued. "
        "Verify at https://verify-kyc.example/go . Call +91 90000 00042.")

SPARSE = "Someone rang about a delivery. Mum hung up. She did not write anything down."


def _report(rid: str, content: str) -> Report:
    return Report(report_id=rid, community_id="test-coalition",
                  received_at=datetime.now(timezone.utc), channel=Channel.SMS,
                  raw_content=content, reporter_pseudonym="resident-101",
                  reporter_area="411038")


# --------------------------------------------------------------------------
# Fixture-backed tools
# --------------------------------------------------------------------------
def test_every_fixture_result_declares_itself():
    """A judge must never be shown synthetic corroboration dressed as a live lookup."""
    assert fixtures.enabled(), "the demo default is fixture-backed"
    for result in (check_url_reputation("https://verify-kyc.example/go"),
                   domain_age_days("verify-kyc.example"),
                   phone_shape("+91 90000 00042"),
                   check_payment_handle("payee042@ybl")):
        assert result["fixture_backed"] is True
        assert result["source"] == "fixture"
        assert "not a live feed" in result["disclaimer"]


def test_tools_never_claim_benign_when_they_know_nothing():
    """Absence of evidence must not render as a clean bill of health."""
    unknown = check_url_reputation("https://something-nobody-has-a-fixture-for.test/x")
    assert unknown["verdict"] != "benign"


def test_a_reserved_example_domain_is_never_the_institution_it_claims():
    result = domain_age_days("totally-unknown-bank.example")
    assert result["verdict"] == "suspicious"
    assert "cannot be a real institution" in result["detail"]


def test_a_legitimate_domain_can_come_back_benign():
    """The challenge set needs shared-but-innocent infrastructure to exist."""
    assert domain_age_days("example.com")["verdict"] == "benign"


# --------------------------------------------------------------------------
# Trace shape
# --------------------------------------------------------------------------
def test_the_trace_has_no_field_for_chain_of_thought():
    """A narrow schema is the control. Free-form reasoning has nowhere to go.

    Exposing deliberation would put whatever a hostile report pushed into the
    model onto a volunteer's screen and into partner-agency briefs.
    """
    forbidden = {"reasoning", "thoughts", "chain_of_thought", "thinking", "scratchpad",
                 "deliberation", "rationale_long"}
    step_fields = set(Trace.model_fields) | set(
        Trace(report_id="x").model_dump().keys())
    from porchlight.trace import TraceStep
    step_fields |= set(TraceStep.model_fields)
    assert not (forbidden & step_fields), f"trace exposes reasoning: {forbidden & step_fields}"


def test_a_processed_report_records_every_pipeline_step():
    case = process_report(_report("r-trace", RICH))
    steps = [s.step for s in case.trace.steps]
    assert steps == ["intake", "corroboration", "stage", "correlation", "response"]
    assert all(s.outcome for s in case.trace.steps), "every step must say what it did"
    assert case.trace.total_ms >= 0


def test_the_trace_names_which_tools_ran_and_what_they_returned():
    case = process_report(_report("r-tools", RICH))
    corroboration = case.trace.step("corroboration")
    called = {c.tool for c in corroboration.tools_called}
    assert {"check_url_reputation", "check_payment_handle"} <= called
    for call in corroboration.tools_called:
        assert call.argument, "a tool call must record what it was asked about"
        assert call.verdict, "and what came back"
    assert any(c.fixture_backed for c in corroboration.tools_called)


def test_a_sparse_report_names_the_evidence_it_is_missing():
    """This is what makes the corroboration step's job legible."""
    case = process_report(_report("r-sparse", SPARSE))
    intake = case.trace.step("intake")
    assert intake.missing_evidence, "a report with nothing in it must say so"
    assert "no callback number" in intake.missing_evidence
    assert "no beneficiary identifier" in intake.missing_evidence


def test_a_rich_report_is_not_reported_as_missing_everything():
    case = process_report(_report("r-rich", RICH))
    missing = case.trace.step("intake").missing_evidence
    assert "no callback number" not in missing
    assert "no beneficiary identifier" not in missing


def test_the_response_step_states_what_the_policy_boundary_held_back():
    case = process_report(_report("r-gate", RICH))
    outcome = case.trace.step("response").outcome
    assert "held at the policy boundary" in outcome


def test_tool_budget_bounds_what_one_report_can_spend(monkeypatch):
    """A report naming forty URLs must not cost forty tool calls."""
    from porchlight import config

    monkeypatch.setattr(config, "settings", lambda: _settings_with(max_tool_calls=2))
    import porchlight.pipeline as pipeline
    monkeypatch.setattr(pipeline, "settings", lambda: _settings_with(max_tool_calls=2))

    many = RICH + " " + " ".join(
        f"https://verify-{n}.example/p" for n in ("kyc", "customs", "parcel", "refund", "gov"))
    case = process_report(_report("r-budget", many))
    assert case.trace.tool_calls <= 2


def _settings_with(*, max_tool_calls: int):
    from porchlight.config import Settings
    base = Settings()
    return Settings(**{**base.__dict__, "max_tool_calls_per_report": max_tool_calls})


def test_the_recorder_records_a_failing_step_rather_than_losing_it():
    rec = TraceRecorder("r-fail")
    try:
        with rec.step("intake"):
            raise RuntimeError("model unavailable")
    except RuntimeError:
        pass
    step = rec.trace.step("intake")
    assert step is not None
    assert not step.ok
    assert "model unavailable" in step.error
