# What is left, in order

Deadline: **14 Sep 2026, 17:00 PT** = 15 Sep, 05:30 IST. Treat Sunday evening
IST as the wall.

## Done

- Strands multi-agent architecture: `Graph` spine + `Swarm` for stage assessment,
  all seven prompts, structured output on every node
- Deterministic campaign clustering with the false-campaign guards
- Cedar policy set (P001–P005) + in-process shim + parity test
- 60-report corpus with a planted campaign, decoys and 20 injection payloads
- Eval harness with pass/fail gates: **attribution F1 1.00, link precision 1.00 (0 false links), held-out injection recall 0.43**
- 92 tests passing, including the adversarial suite and the demo path
- Two jurisdiction packs (IN / US)
- `server.py` + the single-file coordinator dashboard, with the approval gate
  tested at the endpoint (a report that *claims* an approval token gets nothing)
- `deploy/` — ARM64 Dockerfile on the AgentCore Runtime contract, plus preflight
  scripts for the runtime and the policy engine
- CI: ruff + pytest + red-team suite + the eval gate, all in offline mode

## Remaining — in dependency order

### 1. Credits — **Thu 11 Sep, before 12:00 PT. Hard cutoff.**

Request the $50 AWS promotional credits. No extensions, and everything below
costs money without them.

### 2. ~~`server.py` + dashboard~~ — **done**

Run it: `make run`, then <http://localhost:8080>.

Demo prep is now two clicks instead of a second terminal. To make the campaign
*fire* on camera rather than merely be present when the page loads, hold the
planted campaign one report short of threshold and paste the last one live:

```bash
curl -s -X POST localhost:8080/replay -H 'content-type: application/json'   -d '{"directory":"corpus/seed","exclude":["rpt-0003","rpt-0004","rpt-0005","rpt-0006"]}'
# then paste rpt-0003 into the composer on camera -> newly_escalated: true
```

Before the injection beat, press **Denied only** in the policy panel. Do not
scroll the audit log looking for the refusal on camera.

### 3. AgentCore deployment (half a day) — **must land Thursday**

```bash
bash deploy/setup_policy.sh                    # preflight + engine + policies/cedar/*
PYTHONPATH=src python -m porchlight.cli setup-memory   # -> AGENTCORE_MEMORY_ID
bash deploy/deploy_runtime.sh                  # preflight, then configure && launch
```

`deploy_runtime.sh` refuses to deploy while `PORCHLIGHT_OFFLINE=1`, warns when
memory or the policy engine is unset, and runs the suite first. The image in
`deploy/Dockerfile` is ARM64 and serves `/invocations` and `/ping` on 8080.

Technical Implementation explicitly rewards a live demo link and an AgentCore
deployment. A local-only build loses that twice. **If Thursday ends without a
public URL, execute the cut list rather than hoping for the weekend.**

### 4. Live-mode eval run (2 hours)

Re-run with `PORCHLIGHT_OFFLINE=0` to get the latency figure you can actually
quote. The offline number is pipeline overhead and is labelled as such in the
eval output — do not put it in the video.

Do NOT estimate the manual baseline. docs/manual-baseline.md sets out a
procedure with real coordinators; anything short of running it produces a number
that cannot be defended. As a starting point, the procedure has you take one
report, do the lookups and fill
the NCRP form by hand, with a stopwatch. That measured number is worth more to a
judge than any model benchmark, and `MANUAL_BASELINE_SECONDS` in
`eval/run_eval.py` is currently an estimate you should replace.

### 5. Submission assets (Sat 13)

- Architecture diagram — the mermaid block in `docs/architecture.md` renders
  directly; export it as PNG
- README front page with the eval numbers visible above the fold
- **builder.aws.com post with "Agents for Humans" in the title** — up to 0.6
  bonus points for roughly two hours of writing is the best rate of return
  available to you
- Confirm Apache-2.0 shows in the repo's GitHub **About** panel, not just the
  LICENSE file — it is a stated requirement and it is checked

### 6. Video (Sun 14)

Follow `docs/demo-script.md`. Budget four hours; it will take four.

## Cut list, if Thursday slips

Drop in this order:

1. Jurisdiction pack switch
2. Partner brief draft
3. On-chain / URL feed enrichment (RDAP alone is enough)
4. The Swarm — collapse stage assessment into one agent

**Never drop:** campaign correlation, the policy gate, the live URL, the eval
number.

## The highest-leverage thing that is not code

Get **one real person** — a senior-centre coordinator, a police cyber cell
officer, a local elder-fraud coalition volunteer — on camera for fifteen seconds
saying this is a problem they have. Locally authentic beats locally themed, and
it costs two phone calls.
