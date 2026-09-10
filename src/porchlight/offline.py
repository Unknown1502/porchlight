"""Deterministic stand-ins for every node, used when PORCHLIGHT_OFFLINE=1.

Why this exists: the whole pipeline must be runnable with no AWS account, no
credits burned, and no model variance — otherwise the test suite is flaky, CI
cannot run, and every iteration on the clustering logic costs money. These
produce the same pydantic objects the real agents produce, from rules.

They are NOT a fallback for the demo. The demo runs on Bedrock. Anything here
that looks clever is a heuristic, not a claim.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .correlation import cluster_for_report, escalated_by, meets_threshold
from .config import load_pack, settings
from .models import (
    CampaignMatch,
    CampaignResult,
    CorroborationResult,
    Draft,
    DraftKind,
    IndicatorFinding,
    IntakeResult,
    MoneyRail,
    Report,
    ResponseResult,
    StageAssessment,
    Urgency,
)
from .tools.indicators import extract_indicators
from .tools.sanitize import detect_injection
from .tools.store import iter_records

# ---------------------------------------------------------------------------
# JURISDICTION-NEUTRAL CORE
#
# Nothing below names a country. These are the money rails and script families
# that recur wherever this fraud is run, phrased in terms that are true in any
# of them: a courier collecting cash, an official body demanding a transfer, a
# fake support desk taking remote control. Anything that is only true in one
# place - a national ID scheme, a domestic payment rail, a local utility threat
# - belongs in packs/<name>.yaml and is merged in ahead of these.
# ---------------------------------------------------------------------------
_CORE_RAIL_HINTS = [
    (MoneyRail.CRYPTO, r"\bbitcoin\b|\bcrypto\b|\busdt\b|\bbtc atm\b|wallet address"),
    (MoneyRail.GOLD, r"\bgold\b|\bbullion\b|jewell?er"),
    (MoneyRail.COURIER, r"\bcourier\b|\bpick ?up\b.*\bcash\b|our (?:agent|man) will (?:come|collect)"),
    (MoneyRail.CASH, r"\bwithdraw\b.*\bcash\b|\bcash\b.*\bhand over\b"),
    (MoneyRail.BANK_TRANSFER, r"\btransfer\b|safe account|verification deposit"),
]

_CORE_SCRIPTS = [
    ("parcel seized customs bribe", r"parcel|courier.*(?:seized|customs)|fedex|dhl|customs"),
    ("bank account frozen safe account", r"account (?:is )?(?:frozen|compromised|blocked)|safe account"),
    ("fake tech support remote access", r"microsoft|windows|apple|virus|anydesk|teamviewer|remote access"),
    ("law enforcement impersonation arrest threat",
     r"arrest warrant|non[- ]bailable|money launder|narcotic|criminal case against you"),
    ("utility or service cutoff threat", r"(?:will be )?disconnect|service (?:will be )?suspended|final notice"),
    ("account verification credential harvest", r"verify your (?:account|identity|details)|re-?activate your account"),
]

# Institutions impersonated in any jurisdiction. Local bodies come from the pack.
_CORE_ENTITIES = [
    "police", "customs", "bank", "Microsoft", "Apple", "Amazon", "FedEx", "DHL",
    "tax office", "court",
]

_ISOLATION = [
    (r"do not (?:tell|inform|discuss)|don'?t tell (?:anyone|your)", "secrecy demanded"),
    (r"stay on (?:the )?(?:line|call)|do not (?:hang up|disconnect)", "continuous contact"),
    (r"confidential investigation|under investigation|do not compromise", "investigation framing"),
    (r"if (?:the )?bank asks|tell them (?:it'?s|it is)", "coached to deceive"),
    (r"arrest|non[- ]bailable|warrant|jail|deport", "fear framing"),
]


_DEADLINE_RE = re.compile(r"\b(?:within|in)\s+\d+\s*(?:minutes?|hours?|days?)\b", re.IGNORECASE)



@lru_cache(maxsize=8)
def _pack_vocabulary(pack_name: str) -> dict:
    """Merge the active pack's detection vocabulary ahead of the neutral core.

    Local patterns are tried first so that, say, a domestic payment rail is
    labelled as itself rather than as the generic "bank transfer" the core would
    fall back to. Cached because this runs once per report and the pack is a file.
    """
    pack = load_pack(pack_name)
    rails = [(MoneyRail(e["rail"]), e["pattern"]) for e in pack.rail_patterns
             if e.get("rail") in MoneyRail._value2member_map_]
    scripts = [(e["fingerprint"], e["pattern"]) for e in pack.script_patterns
               if e.get("fingerprint") and e.get("pattern")]
    currencies = [(e["currency"], e["pattern"]) for e in pack.currency_patterns
                  if e.get("currency") and e.get("pattern")]
    entities = list(pack.impersonated_entities) + _CORE_ENTITIES
    return {
        "rails": rails + _CORE_RAIL_HINTS,
        "scripts": scripts + _CORE_SCRIPTS,
        "currencies": currencies,
        "entity_re": re.compile(
            r"\b(" + "|".join(re.escape(e) for e in entities) + r")\b", re.IGNORECASE
        ) if entities else None,
    }


def _deadline(text: str) -> str:
    m = _DEADLINE_RE.search(text)
    return m.group(0) if m else ""


def _first(patterns, text, default):
    for value, rx in patterns:
        if re.search(rx, text, re.IGNORECASE):
            return value
    return default


def offline_intake(report: Report) -> IntakeResult:
    text = f"{report.raw_content}\n{report.volunteer_note}"
    inj, evidence = detect_injection(report.raw_content)
    vocab = _pack_vocabulary(settings().pack_name)

    ent = ""
    if vocab["entity_re"]:
        m = vocab["entity_re"].search(text)
        if m:
            ent = m.group(0)

    currency, amount = "", None
    for code, pattern in vocab["currencies"]:
        am = re.search(rf"(?:{pattern})\s?([\d,]{{3,12}})", text, re.IGNORECASE)
        if am:
            currency = code
            try:
                amount = float(am.group(1).replace(",", ""))
            except ValueError:
                amount = None
            break

    return IntakeResult(
        impersonated_entity=ent,
        pretext=(text.strip().split("\n")[0])[:220],
        money_rail=_first(vocab["rails"], text, MoneyRail.NONE),
        amount_demanded=amount,
        currency=currency,
        deadline_claimed=_deadline(text),
        indicators=extract_indicators(text),
        script_fingerprint=_first(vocab["scripts"], text, "unclassified script"),
        contains_injection_attempt=inj,
        injection_evidence=evidence[0] if evidence else "",
    )


def offline_corroboration(report: Report, intake: IntakeResult) -> CorroborationResult:
    from .tools.indicators import indicator_keys

    keys = sorted(indicator_keys(intake.indicators))
    s = settings()
    prior, reporters = [], set()
    for rec in iter_records(s.community_id, s.campaign_window_days):
        if rec["report_id"] == report.report_id:
            continue
        if set(rec.get("indicator_keys", [])) & set(keys) or \
           rec.get("script_fingerprint") == intake.script_fingerprint:
            prior.append(rec["report_id"])
            reporters.add(rec.get("reporter_pseudonym", rec["report_id"]))

    findings = [
        IndicatorFinding(indicator=k.split(":", 1)[1], kind=k.split(":", 1)[0],
                         source="lookup_prior_reports",
                         verdict="suspicious" if prior else "unknown",
                         detail=f"seen in {len(prior)} prior report(s)" if prior else "no prior sighting")
        for k in keys[:8]
    ]
    if len(prior) >= 2 and len(reporters) >= 2:
        conf = "strong"
    elif prior:
        conf = "moderate"
    elif keys:
        conf = "weak"
    else:
        conf = "none"
    return CorroborationResult(
        findings=findings,
        prior_reports_matched=prior,
        corroboration_summary=(
            f"{len(prior)} prior report(s) from {len(reporters)} distinct reporter(s) share an "
            f"indicator or the script fingerprint '{intake.script_fingerprint}'."
            if prior else "No prior community report shares these indicators."
        ),
        external_confidence=conf,
    )


def offline_stage(report: Report, intake: IntakeResult) -> StageAssessment:
    text = f"{report.raw_content}\n{report.volunteer_note}".lower()
    signals = [label for rx, label in _ISOLATION if re.search(rx, text)]
    moved = bool(re.search(r"already (?:sent|paid|transferred)|has (?:sent|paid)|money (?:is )?gone", text))
    imminent = bool(re.search(r"on (?:the )?(?:call|line) now|at the bank|on (?:her|his) way|courier (?:is )?coming|withdrawing", text))

    if moved:
        urgency, hours, stage = Urgency.BLACK, 0.0, "Funds already moved; recovery window is open but closing."
    elif imminent or (intake.money_rail in {MoneyRail.CASH, MoneyRail.GOLD, MoneyRail.COURIER}):
        urgency, hours, stage = Urgency.RED, 2.0, "Extraction underway or a hand-over is scheduled."
    elif intake.money_rail not in {MoneyRail.NONE, MoneyRail.UNKNOWN}:
        urgency, hours, stage = Urgency.AMBER, 24.0, "Payment instruction given; nothing moved yet."
    else:
        urgency, hours, stage = Urgency.GREEN, None, "Contact made; no payment instruction yet."

    if signals and urgency is Urgency.AMBER:
        urgency, hours = Urgency.RED, 6.0

    action = {
        Urgency.BLACK: "Call the resident now and start the recovery checklist; notify the bank partner.",
        Urgency.RED: "Phone the resident immediately; if isolation signals are present, ask the bank partner or a welfare check to make contact instead.",
        Urgency.AMBER: "Call the resident today, before any transfer is attempted.",
        Urgency.GREEN: "Log it. No individual contact needed unless a campaign fires.",
    }[urgency]

    return StageAssessment(
        urgency=urgency,
        hours_to_irreversible=hours,
        playbook_stage=stage,
        isolation_signals=signals,
        rationale=(
            f"Rail={intake.money_rail.value}; movement={'yes' if moved else 'no'}; "
            f"imminent={'yes' if imminent else 'no'}; isolation signals={len(signals)}."
        ),
        recommended_human_action=action,
    )


def offline_campaign(report: Report, intake: IntakeResult) -> CampaignResult:
    s = settings()
    recs = list(iter_records(s.community_id, s.campaign_window_days))
    c = cluster_for_report(report.report_id, recs)
    if c is None or not meets_threshold(c):
        return CampaignResult(is_campaign=False)
    label = (sorted(f for f in c.fingerprints if f) or ["unnamed script"])[0]
    return CampaignResult(
        is_campaign=True,
        newly_escalated=escalated_by(report.report_id, recs),
        match=CampaignMatch(
            campaign_id=c.campaign_id,
            campaign_label=f"Crew running '{label}'",
            member_report_ids=c.report_ids,
            shared_indicators=sorted(c.shared_indicators),
            shared_script_fingerprint=label,
            first_seen=c.first_seen,
            last_seen=c.last_seen,
            distinct_reporters=c.distinct_reporters,
            areas_affected=c.areas,
            confidence=c.confidence(),
            why=(
                f"{len(c.members)} reports from {c.distinct_reporters} residents in "
                f"{max((c.last_seen - c.first_seen).days, 1)} day(s), sharing "
                f"{', '.join(sorted(c.shared_indicators)) or 'the same script'}"
                + (f", {len(c.areas)} area(s): {', '.join(c.areas)}." if c.areas else ".")
            ),
        ),
    )


def offline_response(report: Report, intake: IntakeResult, stage: StageAssessment,
                     campaign: CampaignResult) -> ResponseResult:
    from .config import active_pack

    pack = active_pack()
    drafts: list[Draft] = []
    script = intake.script_fingerprint.replace("_", " ")

    if campaign.is_campaign and campaign.match:
        m = campaign.match
        drafts.append(Draft(
            kind=DraftKind.COMMUNITY_SMS,
            title=f"Warning — {m.campaign_label}",
            intended_recipient=f"{pack.display_name} coalition resident list",
            reading_level_note="Written for a reader aged 70+, plain words, no jargon.",
            body=(
                f"NEIGHBOURHOOD ALERT: callers pretending to be {intake.impersonated_entity or 'officials'} "
                f"are telling people {script}. {m.distinct_reporters} neighbours have been contacted this week. "
                f"It is convincing — do not feel foolish. Hang up, then call us on "
                f"{pack.reporting.get('local_helpline', 'the centre')} before paying anyone."
            )[:320],
        ))
        drafts.append(Draft(
            kind=DraftKind.COMMUNITY_FLYER,
            title=f"What this call sounds like — {m.campaign_label}",
            intended_recipient="Senior centre, library and clinic noticeboards",
            reading_level_note="Large type, short lines, for a noticeboard.",
            body=(
                f"WHAT THEY SAY\n  They claim to be {intake.impersonated_entity or 'an official body'}.\n"
                f"  They tell you: {intake.pretext}\n\n"
                f"WHAT THEY ASK FOR\n  Payment by {intake.money_rail.value.replace('_', ' ')}.\n\n"
                f"WHAT TO DO\n  1. Hang up. A real official will never demand payment on a call.\n"
                f"  2. Tell one person you trust.\n"
                f"  3. Call {pack.reporting.get('local_helpline', 'the centre')}.\n\n"
                f"This has reached {m.distinct_reporters} neighbours in {', '.join(m.areas_affected) or 'our area'} "
                f"in the last few days."
            ),
        ))
        drafts.append(Draft(
            kind=DraftKind.PARTNER_BRIEF,
            title=f"Partner brief — {m.campaign_id}",
            intended_recipient=", ".join(p.get("name", "") for p in pack.partners) or "coalition partners",
            body=(
                f"CAMPAIGN {m.campaign_id} — {m.campaign_label}\n"
                f"Reports: {len(m.member_report_ids)} | Distinct residents: {m.distinct_reporters}\n"
                f"Window: {m.first_seen:%Y-%m-%d} to {m.last_seen:%Y-%m-%d}\n"
                f"Areas: {', '.join(m.areas_affected) or 'n/a'}\n"
                f"Shared indicators: {', '.join(m.shared_indicators) or 'none (script match only)'}\n"
                f"Script: {m.shared_script_fingerprint}\n"
                f"Rail: {intake.money_rail.value}\n"
                f"Confidence: {m.confidence}\n\n"
                f"ASK: freeze/flag the listed beneficiary identifiers; confirm whether other customers "
                f"in these areas have made matching transfers in the window."
            ),
        ))

    drafts.append(Draft(
        kind=DraftKind.OFFICIAL_COMPLAINT,
        title=f"{pack.reporting.get('portal_name', 'Complaint')} — draft for {report.reporter_pseudonym}",
        intended_recipient=pack.reporting.get("portal_name", "national reporting portal"),
        body=(
            f"Channel: {pack.reporting.get('portal_name')} ({pack.reporting.get('portal_url')})\n"
            f"Helpline: {pack.reporting.get('helpline', 'n/a')}\n"
            f"Incident date: {report.received_at:%Y-%m-%d}\n"
            f"Contact method: {report.channel.value}\n"
            f"Impersonated entity: {intake.impersonated_entity or '[RESIDENT TO CONFIRM]'}\n"
            f"Description: {intake.pretext}\n"
            f"Payment method requested: {intake.money_rail.value}\n"
            f"Amount: {intake.amount_demanded or '[RESIDENT TO CONFIRM]'} {intake.currency}\n"
            f"Suspect identifiers: {', '.join(intake.indicators.phone_numbers + intake.indicators.upi_ids + intake.indicators.crypto_addresses) or 'none recorded'}\n"
            f"Complainant details: [RESIDENT TO SUPPLY — name, contact, bank reference]\n\n"
            f"NOTE: submit as soon as possible. Faster reporting improves the chance the receiving "
            f"account can be frozen."
        ),
    ))

    if stage.urgency in {Urgency.BLACK, Urgency.RED}:
        steps = pack.reporting.get("recovery_steps", [])
        drafts.append(Draft(
            kind=DraftKind.VICTIM_CHECKLIST,
            title="The next sixty minutes",
            intended_recipient=report.reporter_pseudonym,
            reading_level_note="Numbered, literal, for someone in shock.",
            body="\n".join(f"{i}. {s_}" for i, s_ in enumerate(steps, 1)) or
                 "1. Call your bank's fraud line now.\n2. Report to the national channel.\n"
                 "3. Do not send anything further, whatever you are told.",
        ))

    if campaign.is_campaign and campaign.match:
        decision = (
            f"Approve the SMS warning to the {', '.join(campaign.match.areas_affected) or 'coalition'} list — "
            f"{campaign.match.distinct_reporters} residents hit by this crew since "
            f"{campaign.match.first_seen:%d %b}."
        )
    elif stage.urgency in {Urgency.RED, Urgency.BLACK}:
        decision = f"{stage.recommended_human_action}"
    else:
        decision = "No decision needed. Logged and watching for a matching pattern."

    return ResponseResult(drafts=drafts, decision_for_human=decision)
