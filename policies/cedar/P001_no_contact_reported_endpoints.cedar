// P001 — never contact infrastructure that came out of a report.
//
// Every URL, phone number and wallet in a report was chosen by the fraudster.
// Fetching one leaks the coalition's presence and IP, tells the crew their
// script is being analysed, and is the single most likely thing a prompt
// injection will try to induce. There is no legitimate case for it, so this is
// a forbid with no exception clause — forbid always wins in Cedar.
//
// AgentCore form; `<GATEWAY_ARN>` is substituted by deploy/setup_policy.sh.
// See policies/README.md for what has and has not been verified against AWS.
forbid (
  principal,
  action in [
    AgentCore::Action::"PorchlightTools___http_fetch",
    AgentCore::Action::"PorchlightTools___sms_send",
    AgentCore::Action::"PorchlightTools___email_send",
    AgentCore::Action::"PorchlightTools___voice_call"
  ],
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
)
when {
  context.input has target_origin &&
  context.input.target_origin == "reported_artefact"
};
