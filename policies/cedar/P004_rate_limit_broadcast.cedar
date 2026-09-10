// P004 — at most two community broadcasts per rolling 24 hours.
//
// Alert fatigue in a senior-centre list is a real operational cost. The third
// warning in a day is the one that teaches people to ignore the second.
//
// Note a deliberate divergence, asserted by tests/test_policy_parity.py rather
// than left to be discovered: this file allows a `human_override`, and the
// in-process engine implements no such path. A coordinator approval satisfies
// P002; it does not buy a third broadcast. If the override is ever wanted, it
// has to be built and tested, not inherited by accident from a comment.
forbid (
  principal,
  action in [
    AgentCore::Action::"PorchlightTools___broadcast_send",
    AgentCore::Action::"PorchlightTools___sms_send"
  ],
  resource == AgentCore::Gateway::"<GATEWAY_ARN>"
)
when {
  context.input has broadcasts_in_last_24h &&
  context.input.broadcasts_in_last_24h >= 2
}
unless {
  context.input has human_override && context.input.human_override == true
};
