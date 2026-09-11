# The demo, beat by beat

Two cuts: **3:00** if you want margin, **5:00** to use the full allowance. Both
land the same three moments — one causal chain, not three separate features:

> Reports arrive unattended → they turn out to be one campaign → the agent
> swarm judges that bounded evidence → the coordinator gets one decision → an
> attacker tries to forge authorization → the system refuses, on the record.

The report was never the threat on its own. The campaign was — and a report
that also tries to talk its way into sending something is not an exception to
that story, it is the same story tested under attack.

- **Discovery** — reports that arrived unattended turn out to be one crew.
  **Before 1:40.**
- **Judgment** — the swarm assesses the bounded evidence a deterministic
  module built, not evidence it invented itself. **Before 2:40.**
- **Trust** — a hostile report tries to use the system and is refused, on the
  record. **Before 4:30** in the long cut.

Everything else is scaffolding.

---

## Before you record

```bash
python tasks.py check     # if this is green, the demo is green
python tasks.py run       # inbox on http://localhost:8080
```

`tests/test_server.py::test_the_hero_scenario` walks the campaign-discovery and
approval beats below end to end — you are not rehearsing something that might
drift; you are demonstrating a regression test. The trace panel (beat 4,
"Agentic judgment") is a dashboard view, checked separately by
`tests/test_server.py::test_case_detail_carries_the_pipeline_trace_per_report`
and `tests/test_trace_and_tools.py`, not by the hero-scenario test itself.

On camera, in the browser only:

1. **Reset demo**
2. **Load prior reports** — note the hint line; it names the reports held back
3. Send one of those held-back reports to the intake webhook (`POST /reports`
   — there is no manual-entry composer in the dashboard, deliberately: reports
   arrive from partner systems, never from someone typing into the coordinator
   screen). The dashboard has no idea this is coming; watch it react live.
4. Open the case, scroll to **How this report was assessed** — the per-step
   trace: which node was a rule and which was a real Strands agent, what each
   used, what it concluded
5. Approve
6. Send the hostile report the same way, open **Policy decisions**

Do **not** warm the store from the terminal first. The old instructions here
told you to, and it loaded every campaign member, so the campaign was already on
screen and Moment 1 had nothing to show.

---

## The 5:00 cut

| Time | Beat | On screen | The line |
|---|---|---|---|
| 0:00–0:15 | **Hook** | The inbox, empty, then the report list | "These reports look unrelated. They're not." |
| 0:15–0:50 | **The problem** | Say who is in the network | One volunteer, reports arriving one at a time — adult protective services, police, a partner bank, an area agency on aging, coordinated part-time. The CFPB says hundreds of US counties run one. Each report is unremarkable alone; nobody has the hours to notice four of them were the same crew. |
| 0:50–1:15 | **It already ran** | Click **Load prior reports** | "These came in over the weekend. Nobody was watching — the agent worked them anyway." Read the line at the top: *worked through 47 reports, folded in the repeats.* Point out: 54 reports, 0 campaigns, nothing waiting. |
| 1:15–1:40 | **Discovery** | Send a held-back report to the webhook; watch the dashboard react with nobody touching it | Campaign count: 0 → 1. One decision arrives. *"Three residents. Three separate phone calls. Same callback number, same payment handle, same story."* **No one person could have seen this.** |
| 1:40–2:40 | **Agentic judgment** | Open the case → **How this report was assessed** | Point at the per-step table. "Correlation — the part that decided these reports are the same crew — is a deterministic module, always, in every run. It is not something we let the model decide, because we tried that and it went wrong; more on that in a moment. What the agents *do* judge is bounded: three assessors — script, money-rail, isolation — hand off to each other, then a campaign agent judges the cluster this module already built. That row says which steps were real Strands agents and which were rule-based in this run." |
| 2:40–3:20 | **What it prepared** | The decision card | States what changed, lists the linking evidence in plain words, has the warning already written. "The agent drafts, a human sends — a rule outside the model, not a line in a prompt." |
| 3:20–3:40 | **The human decision** | Edit one sentence, then **Approve and send** | "Approving is bound to this exact wording. Change a word and the approval no longer applies." Show the sandbox outbox. |
| 3:40–4:30 | **The attack** | Send the hostile report (forged `APPROVAL_TOKEN`) to the same webhook; open **Policy decisions**, **Show denied only** | Denials, tagged to that report, marked *from report text*. "This report tried to mint its own approval and asked us to broadcast its text. Both refused, on the record, tagged to this report id. Report content cannot manufacture authority." Then: "it is still triaged as a real scam — the fraud underneath did not get lost because the text was also hostile." |
| 4:30–4:55 | **Why this matters** | The README results table, ten seconds, no longer | F1 1.00 across five seeds, zero false links, 200 tests, CI green on Linux. Say the weak number out loud too: held-out injection recall is 0.29 — the detector is a signal, not the control; the policy boundary is. Porchlight turns disconnected reports into campaign-level evidence while keeping consequential authority outside untrusted content. |
| 4:55–5:00 | **Close** | Cut to black or the title card | One sentence, nothing added after it: "The system does not give a model unrestricted authority — it judges where judgment helps, and refuses everywhere else." |

## The 3:00 cut

Keep: the hook, **It already ran** through **Discovery** compressed to 40
seconds total, **Agentic judgment** cut to 25 seconds (just the per-step table
and the one line about correlation never being model-judged), **The attack**
cut to 30 seconds, and a 10-second close on the measured results plus the
final sentence.

Drop: the "who it's for" detail beyond one clause folded into the opening,
**What it prepared** as its own beat (fold into the human-decision beat), the
edit-invalidates-approval detail.

Never drop: the campaign forming, the agentic-judgment table, the policy
denial, and the sentence *"no one person could have seen this."*

---

## Things not to claim on camera

- **Do not show CloudWatch and do not say "denied at the gateway."** No Gateway
  exists. The denial you are showing is real and enforced outside the model, but
  it runs in-process. The inbox badge says "local Cedar-equivalent shim" — leave
  it visible. A judge who notices you were straight about it believes the rest.
- **Do not claim a time saving.** There is no measured baseline. If asked, say
  so and point at `docs/manual-baseline.md`.
- **Do not quote the in-vocabulary injection number alone.** Say the held-out
  one. Volunteering a weak number you could have hidden buys more credibility
  than the number costs.
- **Do not imply anything is deployed.** The container is verified; nothing is
  running in AWS.
- **Do not imply the corpus is real.** Say "synthetic, reconstructed from
  publicly documented patterns" once, on screen, and move on.
- **Do not say "AI-powered."**

## If something goes wrong live

The inbox polls every eight seconds, so a slow beat usually resolves itself. If
the campaign does not appear, you sent a report that was not held back — the
hint line after **Load prior reports** names the ones that were. **Reset demo**
returns to a clean state without touching a file.
