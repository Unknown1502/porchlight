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
| 1 | Text description of the project | ◻ not written | agent → `docs/submission-copy.md` |
| 2 | **Public** repo URL | ◻ repo is local-only, never pushed | **HUMAN** |
| 3 | All source + setup instructions to run it | ✅ `README.md` quick start, `make install` verified against pinned deps | done |
| 4 | MIT or Apache licence **visible in the repo About section** | ⚠ `LICENSE` (Apache-2.0) is at the repo root and declared in `pyproject.toml`; the *About section* is a GitHub UI field that only appears once the repo is pushed | **HUMAN** |
| 5 | README | ✅ present, claims table, deployment status table | done |
| 6 | Architecture diagram (image in repo) | ◻ mermaid source exists in `docs/architecture.md`; no rendered image | agent |
| 7 | Demo video ≤ 5 min on YouTube/Vimeo, covering (1) problem (2) who it's for (3) why it matters | ◻ not recorded | **HUMAN** (script: `docs/demo-script.md`) |
| 8 | AWS Builder ID | ◻ unknown whether one exists | **HUMAN** |
| 9 | Live demo link (optional, strengthens Technical Implementation) | ◻ nothing deployed | **HUMAN** approval + agent |
| 10 | Pre-existing work disclosure | ✅ `SUBMISSION-PREEXISTING.md` — **§4 attestations still unticked** | **HUMAN** to confirm |
| 11 | All materials in English | ✅ | done |
| 12 | Judges can test free of charge through the judging period | ⚠ offline mode needs no AWS account and no key; verify this stays true for whatever is deployed | agent |

## Bonus (up to +0.6)

| # | Requirement | Status | Owner |
|---|---|---|---|
| 13 | builder.aws.com post(s) about the build journey, **publicly published before the deadline** | ◻ not drafted | agent drafts → **HUMAN** publishes |

Note on wording: the current published rule asks for *"Agents for Humans" in your
title* — not a hashtag. Multiple posts are permitted. Confirm the exact wording on
the Devpost rules tab before publishing.

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
