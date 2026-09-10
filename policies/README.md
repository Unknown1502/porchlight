# Policy layer

One rule set, two implementations, and one of them is not deployed. Both facts
matter, so both are stated up front.

| Where | What it is | Status |
|---|---|---|
| `src/porchlight/policy.py` | The enforcement point **today** — the same five rules, evaluated in-process, outside the model | **Live.** Every decision in the demo, the tests and the container comes from here |
| `policies/cedar/*.cedar` | The AgentCore Policy deployment form of those rules | **Not loaded.** Blocked on building an AgentCore Gateway |

## What was verified against AWS

Checked against a live account (`bedrock-agentcore-control`, `us-west-2`), not
inferred from documentation:

- **The service is real and the API works.** `CreatePolicyEngine` /
  `CreatePolicy` / `GetPolicy` / `ListPolicies` all exist and respond.
  `deploy/setup_policy.sh --apply` creates the engine, waits for `ACTIVE`, and is
  idempotent on re-run.
- **Cedar is accepted directly.** `definition.cedar.statement` takes Cedar source
  text, with `enforcementMode` of `ACTIVE` or `LOG_ONLY` and `validationMode` of
  `FAIL_ON_ANY_FINDINGS` or `IGNORE_ALL_FINDINGS`.
- **Engine names cannot contain a hyphen.** `^[A-Za-z][A-Za-z0-9_]*$`. The
  natural default, `porchlight-policy`, is rejected; the script uses
  `porchlight_policy` and validates the name before calling AWS.

## What is blocked, and why

Three constraints, discovered by trying:

1. **A wildcard resource is rejected outright.** *"a wildcard resource was
   detected. To avoid unexpected behavior changes, please constrain the resource
   either to a specific AgentCore::Gateway resource or to the AgentCore::Gateway
   resource type."*
2. **Constraining the action forces a specific gateway.** Scoping to the gateway
   *type* works only for policies that do not name an action. As soon as a policy
   names one: *"a constrained action scope was encountered, please constrain the
   resource to a specific AgentCore::Gateway resource when creating tool-specific
   policies."* A fabricated ARN is rejected as invalid.
3. **A `CreatePolicy` success is not a created policy.** Five gateway-type-scoped
   `forbid` rules were accepted by the API and then all five landed in
   `CREATE_FAILED`:

   > Overly Restrictive: Policy Engine will deny every request for the specified
   > principal (AgentCore::IamEntity), action (Any Future Tools) and resource
   > (gateway/\*) combination if the policy is added or updated

   The status has to be read back. `deploy/setup_policy.sh` does exactly that and
   refuses to report success on a `CREATE_FAILED` policy.

All five Porchlight rules constrain actions, so all five need a gateway to point
at. There is no gateway, so none of them are loaded. The `.cedar` files carry a
`<GATEWAY_ARN>` placeholder that the setup script substitutes once one exists.

## Action naming

AgentCore action ids are `<TargetName>___<tool_name>` and cannot contain a dot,
so the engine's `sms.send` is written `AgentCore::Action::"PorchlightTools___sms_send"`
on the wire. `tests/test_policy_parity.py` translates between the two spellings
in one function, so a change to the convention changes one place.

## The rules

| Id | Rule |
|---|---|
| P001 | Never contact infrastructure that came out of a report. No exception clause. |
| P002 | Nothing reaches a resident without an approval capability minted by the coordinator flow — registered, role-checked, scoped to one action, single-use. |
| P003 | Case files, partner briefs and complaint drafts never carry an unmasked payment card or government identity number. |
| P004 | At most two community broadcasts per rolling 24h. A coordinator approval satisfies P002; it does not buy a third broadcast. |
| P005 | Default deny. Only named actions exist as capabilities at all. |

## Parity

`tests/test_policy_parity.py` asserts more than matching ids — an earlier version
checked only that every `P00x` appeared in both places, which two rule sets
agreeing on nothing but their labels would satisfy, and they had in fact already
diverged on P002. It now asserts:

- every action named in Cedar exists in the in-process allow-list, and vice
  versa — an action nobody wrote a rule about is an unreviewed capability;
- P002 demands the same evidence in both: a non-empty token **and**
  `approver_role == "coalition_coordinator"`, with the role string read out of the
  Cedar file and compared to the constant the engine uses;
- default-deny really is the default.

One divergence is deliberate and asserted rather than left to be discovered: the
Cedar P004 allows a `human_override`, and the in-process engine implements no
such path. If that override is ever wanted it has to be built and tested, not
inherited from a comment.
