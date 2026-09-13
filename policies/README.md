# Policy layer

One rule set, two implementations. As of 2026-09-13 both are real and checked
against AWS, but they are still not the same enforcement path — that gap is
stated precisely in "What is still not true" below rather than left implicit.

| Where | What it is | Status |
|---|---|---|
| `src/porchlight/policy.py` | The enforcement point for every request the running app actually serves — the same five rules, evaluated in-process, outside the model | **Live.** Every decision in the demo, the tests and the container comes from here |
| `policies/cedar/*.cedar` | The AgentCore Policy deployment form of those rules | **Loaded and ACTIVE** against a real `AgentCore::Gateway` (see below) — but the running app does not call that gateway |

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
- **A Gateway now exists and the policies are loaded against it.** On
  2026-09-13, `PorchlightGateway`
  (`arn:aws:bedrock-agentcore:us-west-2:899427357316:gateway/porchlightgateway-jtekgeso0t`)
  was created with `bedrock-agentcore-control create-gateway`, an MCP protocol,
  `AWS_IAM` authorization, and the policy engine above attached via
  `policyEngineConfiguration` in `ENFORCE` mode. A gateway target named
  `PorchlightTools`, backed by a stub Lambda
  (`deploy/gateway_target_lambda.py`), registers the 15 tool names the
  Cedar files reference — Cedar validation rejects an action nobody registered,
  so the target has to exist before the policies can even parse. With the
  target in place, `deploy/setup_policy.sh --apply GATEWAY_ARN=...` loaded all
  five policies and **`list-policies` confirms all five reached `ACTIVE`**, not
  merely that `CreatePolicy` returned 200.

## What is still not true

The Gateway being real does not mean tool calls flow through it. **The running
server still calls `src/porchlight/policy.py` directly for every enforcement
decision** — nothing in `agents/factory.py` or the tool layer invokes
`PorchlightGateway`'s MCP endpoint. The stub Lambda behind the `PorchlightTools`
target exists only so Cedar's validator has real tool names to check the five
policies against; it is never invoked by a live report. Routing actual tool
calls through the Gateway — so an `AuthorizeAction` decision from the attached
policy engine is what permits or denies them, not this Python module — is
separate, larger work: real Lambda handlers (or an OpenAPI target) per tool,
IAM wiring for each, and a Strands tool layer that calls the Gateway's MCP
endpoint instead of the local function. Not started.

## Constraints discovered by trying

Four, in the order they were hit — the first three before a Gateway existed at
all, the fourth once one did:

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
4. **An action nobody registered does not exist, as far as Cedar's validator is
   concerned.** Pointing all five policies at a real but target-less gateway
   produced `unrecognized action AgentCore::Action::"PorchlightTools___sms_send"
   ... did you mean "UnknownTool"?` for every one of the fifteen tool actions.
   The gateway's action vocabulary comes from its registered targets, not from
   whatever an engine's Cedar source happens to name. Fixed by creating a
   gateway target named `PorchlightTools` (a Lambda, `deploy/gateway_target_lambda.py`)
   whose inline tool schema declares all fifteen tool names — after which the
   same five files loaded and reached `ACTIVE` unchanged.

All five Porchlight rules constrain actions, so all five need a gateway — and a
target on it naming their actions — to point at. Both now exist
(`PorchlightGateway` / `PorchlightTools`, see above), and `list-policies`
confirms all five are `ACTIVE`. The `.cedar` files still carry a `<GATEWAY_ARN>`
placeholder; `deploy/setup_policy.sh` substitutes it at apply time rather than
hardcoding one identifier the file would go stale against.

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
