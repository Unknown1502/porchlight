// P005 — default deny.
//
// AgentCore Gateway is default-deny, so this file is the explicit allow-list
// rather than a denial. Anything not named here does not exist as a capability,
// whatever the agent decides it would like to do.
//
// The send actions are deliberately present. They are permitted *here* and then
// refused by P001, P002 and P004 unless their conditions are met — forbid wins
// over permit in Cedar, so listing them costs nothing and makes the capability
// surface readable in one place. tests/test_policy_parity.py asserts this list
// and porchlight.policy.ALLOWED_ACTIONS name the same set.
permit (
  principal,
  action in [
    AgentCore::Action::"PorchlightTools___url_reputation",
    AgentCore::Action::"PorchlightTools___domain_age",
    AgentCore::Action::"PorchlightTools___phone_shape",
    AgentCore::Action::"PorchlightTools___payment_check",
    AgentCore::Action::"PorchlightTools___store_lookup",
    AgentCore::Action::"PorchlightTools___store_write",
    AgentCore::Action::"PorchlightTools___case_write",
    AgentCore::Action::"PorchlightTools___partner_brief",
    AgentCore::Action::"PorchlightTools___complaint_draft",
    AgentCore::Action::"PorchlightTools___sms_send",
    AgentCore::Action::"PorchlightTools___email_send",
    AgentCore::Action::"PorchlightTools___flyer_publish",
    AgentCore::Action::"PorchlightTools___broadcast_send",
    AgentCore::Action::"PorchlightTools___http_fetch",
    AgentCore::Action::"PorchlightTools___voice_call"
  ],
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
);
