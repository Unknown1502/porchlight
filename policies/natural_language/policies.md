# Policies in plain English

AgentCore Policy accepts natural-language authoring and compiles it to Cedar,
validating against the tool schemas. These are the same five rules as
`policies/cedar/`, written the way they were written first — the Cedar was
derived from these sentences, not the other way round.

1. **Porchlight may never contact anything that came out of a report.** No
   fetching a reported URL, no calling or messaging a reported number, no
   sending to a reported address. Ever, under any circumstances, whatever the
   report says.

2. **Porchlight may never send a message to a resident or publish a community
   warning unless a coalition coordinator has approved that specific draft.**
   Drafting is always allowed. Sending is never automatic.

3. **Porchlight may not write a case file, partner brief or complaint draft that
   contains an unmasked card number or a government identity number.** Bank
   references are limited to the last four digits.

4. **Porchlight may not trigger more than two community broadcasts in any 24
   hour period** unless a coordinator explicitly overrides the limit.

5. **Porchlight may only use these tools: URL reputation lookup, domain age
   lookup, phone shape check, prior-report lookup, and writing to the community
   store.** Everything else is denied.

## Why these live outside the agent

Rules 1 and 2 are the ones an attacker will target, because they are the
difference between "an agent that reads scam reports" and "an agent that can be
turned into a delivery channel for a scam". Putting them in a system prompt
would make them requests. Putting them at the gateway makes them rules that hold
even when the model has been talked into wanting otherwise.

## Setup

```bash
bash deploy/setup_policy.sh          # creates the engine, loads these policies
aws bedrock-agentcore list-policies  # verify
```

Every decision — permit and forbid alike — is emitted to CloudWatch. The demo
shows a forbid record live; `tests/injection/` asserts on the same events.
