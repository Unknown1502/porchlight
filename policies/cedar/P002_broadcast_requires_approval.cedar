// P002 — nothing reaches a resident without a named human approver.
//
// The agent drafts; a coordinator sends. Expressed as an enforceable rule rather
// than a prompt instruction, so that a model which has been successfully injected
// still cannot send.
//
// AgentCore form. Verified against bedrock-agentcore-control CreatePolicy on
// 2026-09-10: the service requires `resource == AgentCore::Gateway::"<arn>"` for
// any policy that constrains the action, and rejects a Cedar file whose resource
// scope is a wildcard. `<GATEWAY_ARN>` is substituted by deploy/setup_policy.sh.
// Tool action ids take the form `<TargetName>___<tool_name>`.
//
// The in-process engine in src/porchlight/policy.py enforces the same
// requirement — a token minted by the coordinator approval flow, carrying role
// "coalition_coordinator". tests/test_policy_parity.py asserts the two agree on
// that role string rather than merely on the policy id.
forbid (
  principal,
  action in [
    AgentCore::Action::"PorchlightTools___sms_send",
    AgentCore::Action::"PorchlightTools___email_send",
    AgentCore::Action::"PorchlightTools___flyer_publish",
    AgentCore::Action::"PorchlightTools___broadcast_send"
  ],
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
)
unless {
  context.input has approval_token &&
  context.input.approval_token != "" &&
  context.input has approver_role &&
  context.input.approver_role == "coalition_coordinator"
};
