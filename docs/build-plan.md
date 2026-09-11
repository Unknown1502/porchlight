# What is left, in order

Deadline: **Mon 14 Sep 2026, 17:00 PT** = **Tue 15 Sep, 05:30 IST** — confirmed
against the official rules page (`agentsforhumans.devpost.com/rules`, which
states the close date explicitly as Monday, 2026-09-14). The date this file
previously said, "Sunday evening IST", was wrong: the actual wall is early
Tuesday morning IST, roughly a day and a half later than that.

## Done

- Strands multi-agent architecture: 5 agents (intake, corroboration, stage
  `Swarm` of 3 assessors, campaign, response), structured output on every node
- Deterministic campaign clustering with the false-campaign guards
- Cedar policy set (P001–P005) + in-process shim + parity test
- 60-report corpus with a planted campaign, decoys and 20 injection payloads
- Eval harness with pass/fail gates: **attribution F1 1.00, link precision 1.00
  (0 false links), held-out injection recall 0.14–0.57 across 5 seeds (median
  0.29)**
- **200 tests passing**, including the adversarial suite (47 red-team tests)
  and the demo path
- Two jurisdiction packs (IN / US)
- `server.py` + the single-file coordinator dashboard, with the approval gate
  tested at the endpoint (a report that *claims* an approval token gets nothing)
- `deploy/` — ARM64 Dockerfile on the AgentCore Runtime contract, plus preflight
  scripts for the runtime and the policy engine
- CI: ruff + design checks + pytest + red-team suite + corpus regeneration +
  eval gate + 5-seed gate, all in offline mode — **verified green on a fresh
  Linux checkout, Python 3.10 and 3.12, via the actual GitHub Actions run**
- Repo is public, LICENSE now carries the full Apache-2.0 text and is detected
  as such by GitHub (was previously `NOASSERTION` — a truncated LICENSE file)
- `/approve` has an optional per-coordinator shared-secret identity boundary
  (`PORCHLIGHT_COORDINATOR_CREDENTIALS`); unset, it stays labelled dev auth
- The pipeline's per-step trace (which node was a real Strands agent vs. a
  deterministic stand-in) is now persisted and shown on the case page — it
  used to die with the request that produced it

## Remaining — in dependency order

### 1. Credits — **Thu 11 Sep, before 12:00 PT. Hard cutoff.**

Request the $50 AWS promotional credits. No extensions, and everything below
costs money without them. **This is today.** Check the account directly rather
than trust this note — the official resources page has also been seen saying
credits for this hackathon are already fully disbursed, which would make this
moot either way; confirm which is true before spending time on it.

### 2. ~~`server.py` + dashboard~~ — **done**

Run it: `make run`, then <http://localhost:8080>.

Demo prep is now two clicks instead of a second terminal. `/replay` holds back
every crew in the corpus one report short of threshold automatically now
(`hold_campaign_tail` defaults to true, reading the corpus's own ground truth)
— no manual `exclude` list needed any more:

```bash
curl -s -X POST localhost:8080/replay -H 'content-type: application/json' -d '{"directory":"corpus/seed"}'
# -> 54 queued, 6 held back, 0 campaigns. Paste rpt-0003 on camera -> newly_escalated: true
```

Before the injection beat, press **Denied only** in the policy panel. Do not
scroll the audit log looking for the refusal on camera.

### 3. AgentCore deployment (half a day) — **decided against, for now**

```bash
bash deploy/setup_policy.sh                    # preflight + engine + policies/cedar/*
PYTHONPATH=src python -m porchlight.cli setup-memory   # -> AGENTCORE_MEMORY_ID
bash deploy/deploy_runtime.sh                  # preflight, then configure && launch
```

`deploy_runtime.sh` refuses to deploy while `PORCHLIGHT_OFFLINE=1`, warns when
memory or the policy engine is unset, and runs the suite first. The image in
`deploy/Dockerfile` is ARM64 and serves `/invocations` and `/ping` on 8080.

Technical Implementation explicitly rewards a live demo link and an AgentCore
deployment, and a local-only build loses that. **The deliberate call made
during the 2026-09-11 hardening pass was not to attempt this**, weighed
against: the account's `INVALID_PAYMENT_INSTRUMENT` block on Bedrock, three
days left, a pipeline that is green for the first time, and the official rules
stating AgentCore is a strengthening choice, not a requirement. Building a
Gateway integration under that time pressure risks destabilizing a working
submission to chase an optional line item. If credits or a working payment
method land with real time to spare, this is still the single highest-value
remaining code change — but only then.

### 4. Live-mode eval run (2 hours) — **blocked on billing**

Re-run with `PORCHLIGHT_OFFLINE=0` to get the latency figure you can actually
quote. The offline number is pipeline overhead and is labelled as such in the
eval output — do not put it in the video. Blocked account-wide by
`AccessDeniedException: INVALID_PAYMENT_INSTRUMENT` as of this session; an
earlier attempt on the same account did reach Bedrock once, so the wiring is
right and this is a billing problem, not a code problem.

Do NOT estimate the manual baseline. `docs/manual-baseline.md` sets out a
procedure with real coordinators; anything short of running it produces a number
that cannot be defended. As a starting point, the procedure has you take one
report, do the lookups and fill the NCRP form by hand, with a stopwatch. That
measured number is worth more to a judge than any model benchmark.
(`MANUAL_BASELINE_SECONDS` no longer exists in `eval/run_eval.py` — it carried
an unsupported ~254,000× speedup claim built on an unmeasured estimate and was
removed entirely, not left as a placeholder to fill in. Do not reintroduce it
without an actually-timed number behind it.)

### 5. Submission assets — **mostly done**

- Architecture diagram — `docs/architecture.svg` exists, is checked into the
  repo, and its own text (verified this session) states plainly what's
  verified vs. not, including the AWS footprint. ✅
- README front page with the eval numbers visible above the fold — ✅, and its
  numbers were re-verified against live runs this session, not just trusted
- **builder.aws.com post with "Agents for Humans" in the title** — three drafts
  exist in `docs/blog-drafts/`, **still need a human to actually publish them**;
  up to 0.6 bonus points for work that's already written is the best rate of
  return left
- Apache-2.0 in the GitHub **About** panel — ✅ confirmed via the API this
  session (`license.spdx_id: Apache-2.0`); it was silently `NOASSERTION`
  before a truncated LICENSE file was replaced with the full text

### 6. Video — **the one thing still blocking submission**

Follow `docs/demo-script.md` — restructured this session into three beats
(discovery, agentic judgment, trust) with a fourth "how this was assessed"
trace panel now built to make the middle beat concrete on screen. Budget four
hours; it will take four. Record before Monday 14 Sep, not the night before —
the deadline is 17:00 PT / Tuesday 05:30 IST, not Sunday.

## Cut list

Thursday (item 1, above) is today. The AgentCore live-URL item this list used
to protect has already been decided against for this submission — see item 3
— so it is off this list rather than a cut of last resort. What's left to
still not cut, in order of what actually matters if the remaining three days
get tight:

**Never drop:** campaign correlation, the policy gate, the eval number, the
video. Everything else — a jurisdiction-pack switch on camera, the partner
brief draft, live-feed enrichment beyond RDAP — was always optional polish and
still is.

## The highest-leverage thing that is not code

Get **one real person** — a senior-centre coordinator, a police cyber cell
officer, a local elder-fraud coalition volunteer — on camera for fifteen seconds
saying this is a problem they have. Locally authentic beats locally themed, and
it costs two phone calls.
