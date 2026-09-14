# Submission checklist

Every required artifact, its current status, and who has to do it. Rows marked
**HUMAN** cannot be completed by a coding agent — they need an account, a
camera, or a person clicking publish.

**Hard deadline: 2026-09-14, 5:00pm PDT.**

Two dates are not published on the Devpost overview page and must be confirmed
against the official rules tab: the **submission period opening date** (needed for
`SUBMISSION-PREEXISTING.md` §4) and any **AWS credits request deadline**. Do not
assume either.

---

## Required artifacts

| # | Requirement | Status | Owner |
|---|---|---|---|
| 1 | Text description of the project | ✅ drafted in `docs/submission-copy.md`, including an honest-status section | **HUMAN** to paste |
| 2 | **Public** repo URL | ✅ pushed to `https://github.com/Unknown1502/porchlight`; confirmed public via the GitHub API (`"private": false, "visibility": "public"`) | done |
| 3 | All source + setup instructions to run it | ✅ `README.md` quick start, `make install` verified against pinned deps | done |
| 4 | MIT or Apache licence **visible in the repo About section** | ✅ Fixed and confirmed: `LICENSE` now carries the full canonical Apache-2.0 text (was a 17-line summary, not enough for GitHub's detector). Re-checked via the API after pushing — `license.spdx_id` now reads `Apache-2.0` (was `NOASSERTION`) | done |
| 5 | README | ✅ present, claims table, deployment status table | done |
| 6 | Architecture diagram (image in repo) | ✅ `docs/architecture.svg` — pipeline, trust boundary, human boundary, and a row stating what is *not* verified | done |
| 7 | Demo video ≤ 5 min on YouTube/Vimeo, covering (1) problem (2) who it's for (3) why it matters | ◻ not recorded | **HUMAN** (script: `docs/demo-script.md`) |
| 8 | AWS Builder ID | ◻ unknown whether one exists | **HUMAN** |
| 9 | Live demo link (optional, strengthens Technical Implementation) | ◻ nothing deployed | **HUMAN** approval + agent |
| 10 | Pre-existing work disclosure | ✅ `SUBMISSION-PREEXISTING.md` — the submission-period-opening box is now ticked and cited against the official rules page (opened 2026-08-10); **the other two boxes require the author personally**, since they ask whether any material was carried in from elsewhere — nothing in the repo or a public source can answer that | **HUMAN** to confirm the remaining 2 boxes |
| 11 | All materials in English | ✅ | done |
| 12 | Judges can test free of charge through the judging period | ⚠ offline mode needs no AWS account and no key; verify this stays true for whatever is deployed | agent |

## Bonus (up to +0.6)

| # | Requirement | Status | Owner |
|---|---|---|---|
| 13 | builder.aws.com post(s) about the build journey, **publicly published before the deadline** | ✅ three drafts in `docs/blog-drafts/` (background intake · deterministic correlation · approval as capability) | **HUMAN** publishes |

Note on wording: the current published rule asks for *"Agents for Humans" in your
title* — not a hashtag. Multiple posts are permitted. Confirm the exact wording on
the Devpost rules tab before publishing.

## Known blockers

| Blocker | Effect | Status |
|---|---|---|
| **No AWS account can complete an Anthropic Bedrock Marketplace subscription.** Original account: `AccessDeniedException: INVALID_PAYMENT_INSTRUMENT`, unresolved even after adding a Visa card — Bedrock model billing runs through AWS Marketplace, a separate subscription check from general account billing, and Marketplace subscriptions require a credit card specifically; a debit card added to the account does not clear it. A second, brand-new AWS account hit a different wall: new-account verification (cleared after ~2h), then `ValidationException: Operation not allowed` (fixed by submitting Anthropic's first-time use-case form), then a final `AccessDeniedException: ... create a support case` requiring AWS Support to manually clear — not resolvable same-day. | Anthropic's model specifically cannot run on either account. Worked around on 2026-09-13 — see below — so this no longer blocks live mode overall, only the specific model id. | **Confirmed external, worked around.** No credit card available; the block is Anthropic's Marketplace listing specifically, not Bedrock access generally (verified below). Not required by the rules regardless (AgentCore/live mode strengthens Technical Implementation, it isn't mandatory) — but now demonstrated live anyway. |

Worth knowing: an earlier attempt on the original account *did* reach Bedrock and
the intake agent returned a valid structured result from
`global.anthropic.claude-sonnet-4-6`, before this blocker appeared — so the model
id and the Strands live-mode wiring were already confirmed correct in principle.

**2026-09-13: worked around, not just worked around it in principle.** Tested
`converse` directly against this account for `amazon.nova-lite-v1:0`,
`us.amazon.nova-pro-v1:0` and `us.meta.llama3-3-70b-instruct-v1:0` — all three
succeeded on the first call. Only Anthropic's models hit
`INVALID_PAYMENT_INSTRUMENT`; the account's Bedrock access otherwise works.
Setting `PORCHLIGHT_MODEL_ID=us.amazon.nova-pro-v1:0` and nothing else, a full
`process_report()` run completed live — intake, corroboration, stage swarm,
correlation, response — with `case.errors == []`. Confirmed against the
Devpost rules page (fetched 2026-09-13): the requirement is the Strands Agents
SDK, not a specific model vendor, so this is a legitimate substitution.

Getting there surfaced four real, previously-latent bugs — none caused by
switching models, all exposed by finally running live end to end:

1. Amazon Nova Pro emits `null` for an empty list where Claude emits `[]`,
   failing Pydantic validation on every `list[...]` structured-output field.
   Fixed once, at the model layer (`src/porchlight/models.py`,
   `_NoneListsToEmpty`).
2. `pipeline._extract_structured()` read `swarm_result.execution_order`, which
   does not exist on strands-agents 1.55.0's `SwarmResult` (`node_history` is
   the real field), and looked for the parsed model on the wrong object.
   Offline mode never calls this function — `_run_offline` sets `case.stage`
   directly — so it was wrong behind all 200 tests until live mode reached it.
3. The stage swarm's last speaker sometimes ends its turn on plain text
   instead of the structured tool call. Fixed with the same two-phase pattern
   already used for the corroboration node.
4. Wiring `AgentCore Memory` into agent construction (see below) surfaced two
   more: `community_actor_id` built an actor id with a double colon
   (`coalition::id`), and session ids used `:` as a separator — both rejected
   by AgentCore's id patterns, both caught only once a session manager was
   actually constructed against the live service.

All four are fixed and covered by the full green test suite plus this live
verification. `docs/submission-copy.md` and the README's "Deployment status"
section carry the same claims.

**AgentCore Gateway, AgentCore Policy at that gateway, and AgentCore Memory —
also done for real on 2026-09-13**, not carried over from the 2026-09-10/11
sessions:

- `PorchlightGateway` (READY) with the policy engine attached in `ENFORCE`
  mode, a `PorchlightTools` target (a stub Lambda) declaring the 15 tool
  actions the Cedar files reference, and all 5 policies loaded and reading
  back `ACTIVE` — not just `CreatePolicy` returning 200. The running app does
  not yet call this gateway for enforcement; see `policies/README.md` for
  exactly what that does and does not mean.
- `PorchlightCommunityMemory-csMZJnAAJD` (ACTIVE), with every agent role now
  constructed with a real session manager against it, verified by an
  independent `list_events` call after a live agent turn. The structured case
  record store correlation reads is unaffected, by design — see README
  Limitations.

## Pre-flight (run immediately before submitting)

```bash
python tasks.py install    # pinned deps into the current environment
python tasks.py check      # lint + tests + corpus + eval gates, in one command
python tasks.py run        # dashboard on :8080, walk the hero scenario by hand
```

`make <target>` works identically wherever `make` exists; it delegates to the
same file. `make` is absent on a stock Windows install, which is why the
portable form is the one written down.

Then confirm by eye:

- [ ] Every number in the README traces to a runnable command or a citation.
- [ ] The deployment status table matches reality — nothing marked verified that isn't.
- [ ] No AWS account id, key, personal email, or real phone number in the repo.
- [ ] `SUBMISSION-PREEXISTING.md` §4 boxes are ticked truthfully.
- [ ] A judge can clone and run in under ten minutes with no AWS account.
- [ ] Demo video is ≤ 5:00 and actually shows the product working.

## Teardown after judging

Anything billable that was created for the demo:

```bash
# Runtime deployment, done for real on 2026-09-13/14 (not hypothetical —
# see the Deployed to AgentCore Runtime row in README.md):
agentcore destroy --agent porchlight        # deletes the runtime + its STM memory
aws ecr delete-repository --region us-west-2 --repository-name bedrock-agentcore-porchlight --force
aws codebuild delete-project --region us-west-2 --name bedrock-agentcore-porchlight-builder
aws iam list-role-policies --role-name AmazonBedrockAgentCoreSDKRuntime-us-west-2-33fcef5dee \
  --query 'PolicyNames' --output text | tr '\t' '\n' | while read p; do \
  aws iam delete-role-policy --role-name AmazonBedrockAgentCoreSDKRuntime-us-west-2-33fcef5dee --policy-name "$p"; done
aws iam delete-role --role-name AmazonBedrockAgentCoreSDKRuntime-us-west-2-33fcef5dee
aws iam delete-role-policy --role-name AmazonBedrockAgentCoreSDKCodeBuild-us-west-2-33fcef5dee --policy-name CodeBuildExecutionPolicy
aws iam delete-role --role-name AmazonBedrockAgentCoreSDKCodeBuild-us-west-2-33fcef5dee
aws s3 rb s3://bedrock-agentcore-codebuild-sources-899427357316-us-west-2 --force

# Gateway resources added 2026-09-13 (policies/README.md has the full story):
aws bedrock-agentcore-control delete-gateway-target --region us-west-2 \
  --gateway-identifier porchlightgateway-jtekgeso0t --target-id EYJOVCNKSO
aws bedrock-agentcore-control delete-gateway --region us-west-2 \
  --gateway-identifier porchlightgateway-jtekgeso0t
aws lambda delete-function --region us-west-2 --function-name porchlight-gateway-target-stub
aws iam delete-role-policy --role-name PorchlightGatewayExecutionRole --policy-name PorchlightGatewayPolicyEngineAccess
aws iam delete-role-policy --role-name PorchlightGatewayExecutionRole --policy-name PorchlightGatewayInvokeTargetLambda
aws iam delete-role --role-name PorchlightGatewayExecutionRole
aws iam detach-role-policy --role-name PorchlightGatewayTargetLambdaRole --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name PorchlightGatewayTargetLambdaRole
aws logs delete-log-group --region us-west-2 --log-group-name /porchlight/policy-denials

# Policy engine (delete last — the gateway references it):
aws bedrock-agentcore-control delete-policy-engine \
  --region us-west-2 --policy-engine-id porchlight_policy-z8m1tlah9t

# Memory resource added 2026-09-13:
aws bedrock-agentcore-control delete-memory --region us-west-2 \
  --memory-id PorchlightCommunityMemory-csMZJnAAJD
```

Currently live in the author's account, all in `us-west-2`:
- **AgentCore Runtime `porchlight-cOsdTnHogs`** — READY, verified with a real
  `agentcore invoke` that returned a correctly triaged case running live on
  Nova Pro
- ECR repository `bedrock-agentcore-porchlight`, CodeBuild project
  `bedrock-agentcore-porchlight-builder`, S3 bucket
  `bedrock-agentcore-codebuild-sources-899427357316-us-west-2`
- IAM roles `AmazonBedrockAgentCoreSDKRuntime-us-west-2-33fcef5dee`,
  `AmazonBedrockAgentCoreSDKCodeBuild-us-west-2-33fcef5dee`
- Runtime-scoped STM memory (created automatically by `agentcore deploy`,
  deleted by `agentcore destroy` — separate from the app-level Memory below)
- Policy engine `porchlight_policy-z8m1tlah9t` — ACTIVE, 5 policies loaded
- Gateway `porchlightgateway-jtekgeso0t` (`PorchlightGateway`) — READY, policy engine attached in `ENFORCE` mode
- Gateway target `EYJOVCNKSO` (`PorchlightTools`) — READY, backed by the stub Lambda below
- Lambda `porchlight-gateway-target-stub` — never invoked by a live report; exists only so Cedar validation has real tool names to check
- IAM roles `PorchlightGatewayExecutionRole`, `PorchlightGatewayTargetLambdaRole`
- CloudWatch log group `/porchlight/policy-denials` — real policy-denial mirror, used by the CloudWatch row in the README's deployment status table
- Memory `PorchlightCommunityMemory-csMZJnAAJD` — ACTIVE, backs every agent role's conversation session; the structured case record store is unaffected (still a local JSON file)

None of this is expensive to leave running for the ~1 day left before judging,
but all of it should go before it becomes a forgotten line item.
