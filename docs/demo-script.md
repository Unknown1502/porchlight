# The demo, beat by beat

Two cuts: **3:00** if you want margin, **5:00** to use the full allowance. Both
land the same two moments.

The whole thing rests on those two:

- **Discovery** — reports that arrived unattended turn out to be one crew. The
  judge must understand this **before 2:00**.
- **Trust** — a hostile report tries to use the system and is refused, on the
  record. **Before 4:00** in the long cut.

Everything else is scaffolding.

---

## Before you record

```bash
python tasks.py check     # if this is green, the demo is green
python tasks.py run       # inbox on http://localhost:8080
```

`tests/test_server.py::test_the_hero_scenario` walks exactly the sequence below.
You are not rehearsing something that might drift; you are demonstrating a
regression test.

On camera, in the browser only:

1. **Reset demo**
2. **Load prior reports** — note the hint line; it names the reports held back
3. Paste one of those reports into **Add a report**
4. Approve
5. Paste the hostile report, open **Policy decisions**

Do **not** warm the store from the terminal first. The old instructions here
told you to, and it loaded every campaign member, so the campaign was already on
screen and Moment 1 had nothing to show.

---

## The 5:00 cut

| Time | Beat | On screen | The line |
|---|---|---|---|
| 0:00–0:25 | **The problem** | The inbox, empty | "This is one volunteer's Monday. Reports come in one at a time. Each one gets handled and filed. Nobody has the hours to notice that four of them were the same crew." |
| 0:25–0:50 | **Who it's for** | Say who is in the network | Adult protective services, police, a partner bank, an area agency on aging — coordinated by one part-time volunteer. The CFPB says hundreds of US counties run one. Name the repetitive work being lifted. |
| 0:50–1:20 | **It already ran** | Click **Load prior reports** | "These came in over the weekend. Nobody was watching — the agent worked them anyway." Read the line at the top: *worked through 47 reports, folded in the repeats.* Point out: no campaign, nothing waiting. |
| 1:20–2:00 | **Discovery** | Paste a held-back report, **Add report** | The campaign appears and one decision arrives. *"Three residents. Three separate phone calls. Same callback number, same payment handle, same story."* Then the sentence the submission rests on: **no one person could have seen this.** |
| 2:00–2:50 | **What it prepared** | The decision card | It states what changed, lists the linking evidence in plain words, and has the warning already written for someone reading it aloud. Say why the agent cannot send it: "the agent drafts, a human sends — and that is a rule outside the model, not a line in a prompt." |
| 2:50–3:20 | **The human decision** | Edit one sentence, then **Approve and send** | Point at the sandbox badge. "Approving is bound to this exact wording. Change a word and the approval no longer applies." Show the sandbox outbox. |
| 3:20–4:15 | **The wall** | Paste the hostile report; open **Policy decisions**, **Show denied only** | Two denials, tagged to that report, marked *from report text*. "This report asked us to fetch its link and to broadcast its text to residents. Both refused, and the refusal has the report's name on it." Then: "it is still triaged as a real scam — the fraud underneath did not get lost because the text was also hostile." |
| 4:15–4:40 | **How** | `docs/architecture.svg` | Trust boundary first. Clustering is deterministic, so injected prose cannot argue its way into a campaign. Ten seconds a layer, no more. |
| 4:40–5:00 | **Measured, and honest** | The README results table | F1 1.00 across five seeds, zero false links. Then say the weak number out loud: held-out injection recall is 0.29. Explain why that is survivable — the detector is not the control. Close on the coalition, not the stack. |

## The 3:00 cut

Keep: 0:00–0:25, 0:50–2:00, 2:50–3:20 compressed to 20 seconds, 3:20–4:15 cut to
30 seconds, and a 15-second close on the measured results.

Drop: the "who it's for" beat (fold one sentence into the opening), the
architecture diagram, the edit-invalidates-approval detail.

Never drop: the campaign forming, the policy denial, and the sentence *"no one
person could have seen this."*

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
the campaign does not appear, you pasted a report that was not held back — the
hint line after **Load prior reports** names the ones that were. **Reset demo**
returns to a clean state without touching a file.
