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
| **No AWS account can complete an Anthropic Bedrock Marketplace subscription.** Original account: `AccessDeniedException: INVALID_PAYMENT_INSTRUMENT`, unresolved even after adding a Visa card — Bedrock model billing runs through AWS Marketplace, a separate subscription check from general account billing, and Marketplace subscriptions require a credit card specifically; a debit card added to the account does not clear it. A second, brand-new AWS account hit a different wall: new-account verification (cleared after ~2h), then `ValidationException: Operation not allowed` (fixed by submitting Anthropic's first-time use-case form), then a final `AccessDeniedException: ... create a support case` requiring AWS Support to manually clear — not resolvable same-day. | Live model mode cannot run on either account. Offline mode is fully unaffected — the demo, tests, and eval all run without any AWS account. | **Closed, not pursued further.** No credit card available; investigated on 2026-09-11/12, confirmed a hard external constraint rather than a config error. Not required by the rules (AgentCore/live mode strengthens Technical Implementation, it isn't mandatory). |

Worth knowing: an earlier attempt on the original account *did* reach Bedrock and
the intake agent returned a valid structured result from
`global.anthropic.claude-sonnet-4-6`, before this blocker appeared — so the model
id and the Strands live-mode wiring are confirmed correct in principle. That
result stands regardless of the billing blocker above.

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
agentcore destroy                                    # runtime, ECR, CodeBuild, IAM
aws bedrock-agentcore-control delete-policy-engine \
  --region us-west-2 --policy-engine-id <id>         # policy engine
```

Currently live in the author's account: policy engine `porchlight_policy-z8m1tlah9t`
(us-west-2, ACTIVE, zero policies loaded). Nothing else.
