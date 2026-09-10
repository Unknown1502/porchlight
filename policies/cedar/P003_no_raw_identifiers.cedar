// P003 — case files, partner briefs and complaint drafts never carry raw
// identifiers. Bank references are masked to the last four digits; government
// identity numbers never appear at all.
//
// This protects the people the system exists to help: a coalition case file is
// shared across several agencies, and it is exactly the kind of document that
// ends up on a shared drive.
//
// Both attributes are checked because the in-process engine checks both shapes:
// a 13-19 digit payment card, and a government identity number (a 12-digit
// national id or a US SSN). An earlier version of the shim tested only the card
// shape while this file named both, which is the kind of quiet drift
// tests/test_policy_parity.py now exists to catch.
forbid (
  principal,
  action in [
    AgentCore::Action::"PorchlightTools___case_write",
    AgentCore::Action::"PorchlightTools___partner_brief",
    AgentCore::Action::"PorchlightTools___complaint_draft"
  ],
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
)
when {
  (context.input has contains_unmasked_pan && context.input.contains_unmasked_pan) ||
  (context.input has contains_government_id && context.input.contains_government_id)
};
