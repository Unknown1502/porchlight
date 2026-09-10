# Devpost submission copy

Draft text for each required field. Everything here is either measurable by a
command in this repo or cited. Nothing is padded, and nothing claims a
deployment that does not exist.

Before submitting, work through `docs/CLAW-BACK.md` — several required items
(public repo, video, Builder ID) can only be done by a person.

---

## Project name

**Porchlight**

## Tagline (short description)

> One person sees one incident. Porchlight sees the campaign.

## Elevator pitch (~200 characters)

A background agent for the volunteers who run local elder-fraud response
networks. It works the intake queue unattended and interrupts only when reports
from different people turn out to be one crew.

---

## What it does

A county elder-fraud response network — adult protective services, police, a
partner bank, an area agency on aging — is usually coordinated by one part-time
volunteer with about four hours a week. Reports arrive one at a time. Each is
handled, filed, and forgotten. Nobody has the hours to notice that report 7,
report 19 and report 44 describe the same crew working the same postcode.

Porchlight runs in the background and does that noticing.

While nobody is watching, it takes reports from a webhook, deduplicates repeat
calls from the same resident, extracts indicators, runs allow-listed
corroboration tools, merges follow-ups into existing cases, and correlates
across people. When reports from *different* residents turn out to share a crew,
it drafts a community warning and stops.

The coordinator opens the inbox and sees three things, in this order:

1. what Porchlight handled while they were away;
2. **one** prepared decision, carrying the evidence that produced it;
3. the case list, ordered by hours until the money cannot be recovered.

They approve, edit, or reject. That is the whole interaction.

## Who it is for

Not the person being scammed — that would be a personal assistant. The user is
the coordinator of a local elder-fraud prevention and response network. The CFPB
reports that hundreds of US counties have built one.

Their work — intake, corroboration, cross-agency notification, community
education — is exactly the repetitive, judgment-heavy load this hackathon asks
you to lift off a human.

## Why it matters

US adults aged 60+ reported $2.4B in fraud losses to the FTC in 2024, and the
FTC's own modelling puts the true figure between $10.1B and $81.5B once
under-reporting is accounted for.

But the number that matters here is smaller and more specific: a crew working a
neighbourhood does not stop after three victims. The reports already sitting in
a coordinator's inbox contain the evidence that would identify that crew — and
that evidence is lost, one embarrassed phone call at a time, because nobody has
the hours to cross-reference it.

Porchlight is not a better scam detector. It is the thing that reads sixty
reports at once and says: these four are the same people, and they have not
finished.

## How we built it

- **Strands Agents** for the four judgment steps: intake (what happened, and
  what evidence is missing), corroboration (which allow-listed tool would settle
  the gap), stage assessment (a swarm of three assessors arguing about how close
  this resident is to irreversible loss), and campaign judgment (is this cluster
  really one crew).
- **Deterministic correlation, deliberately not a model decision.** Clustering
  is union-find over hard indicators in `src/porchlight/correlation.py`. It is
  reproducible, testable, and — the part that matters — prompt-injected prose
  cannot argue its way into or out of a cluster, because set membership is
  computed from indicators rather than from text.
- **A Cedar policy layer outside the model**, default-deny, every decision
  audited with who asked, which rule answered, and about which report.
- **SQLite** for the queue, cases, approvals and audit trail, so the agent
  survives a restart and a background worker and the web process cannot disagree.
- **A capability-based approval model.** An approval is bound to one action, one
  audience, and the exact message digest, single-use and short-lived. Editing a
  draft invalidates it by construction.

## Challenges we ran into

Three worth naming, because each was a real defect rather than a war story:

**The evaluation was scoring the wrong thing.** It measured only the campaign it
was hoping to find, which rewards a system that clusters everything. Adding
link-level scoring against crew ground truth, plus a four-case challenge set,
dropped link precision from 1.00 to **0.50** and exposed 21 false links. The
cause: two reports sharing *one* indicator were treated as the same crew — so
four unrelated scams that each told the victim to "ring your bank on the number
on your card" were fused into one fictitious campaign. A link now needs two
independent shared indicators, or one plus the same script. Precision returned
to 1.00 with zero false links.

**Live mode was broken behind a green test suite.** Offline mode never exercised
it. Running one report against Bedrock showed `structured_output` on a
tool-bearing agent spending its tool-use turn on the actual tools and then
failing. Tool-bearing agents now converse first, then structure the result.

**The demo was loading the answer.** The dashboard's replay button loaded the
whole corpus, so the campaign was on screen before the coordinator did anything.
The replay endpoint now holds every crew one report short of threshold, read
from the corpus's own ground truth, so the campaign is found live.

## Accomplishments we're proud of

Every number in the README is either produced by a command in the repo or cited
to a source. The ones that were neither were deleted — including a ~254,000×
"speedup" computed by dividing an unmeasured manual baseline by the offline
stub's own latency.

The evaluation reports the number that makes the system look worst: held-out
injection recall of **0.14–0.57 (median 0.29)**, quoted instead of the 1.00 we
score on payloads written against our own signature list.

## What we learned

A benchmark that only contains the case you designed for tells you nothing. The
challenge set took an afternoon and found a bug that would have broadcast a false
campaign to a list of frightened people on the strength of a bank's own
customer-service line.

## What's next

1. Build the AgentCore Gateway and load the five Cedar policies against it, so
   enforcement moves outside the process.
2. Back the record store with AgentCore Memory so community memory survives a
   restart.
3. Put an IdP in front of approvals so the approver is authenticated, not just
   named.
4. Run the manual-baseline study in `docs/manual-baseline.md` with real
   coordinators, and report time *and* accuracy.

## Built with

`python` · `strands-agents` · `amazon-bedrock` · `bedrock-agentcore` ·
`amazon-bedrock-agentcore-policy` · `cedar` · `fastapi` · `sqlite` · `docker`

## Try it out

```bash
git clone <repo>
cd porchlight
python tasks.py install
python tasks.py check     # lint, 184 tests, corpus, eval, 5-seed gate
python tasks.py run       # inbox on http://localhost:8080
```

No AWS account needed: the default mode is offline and fixture-backed, so a judge
can run everything free of charge. Live model mode is one environment variable
(`PORCHLIGHT_OFFLINE=0`) and an AWS region.

In the inbox: **Load prior reports**, then paste one of the held-back reports it
names. A campaign forms and one decision appears.

---

## Honest status, stated in the submission

Judges should not have to discover this by testing:

- **Deployed to AgentCore Runtime: no.** The Runtime contract (`GET /ping`,
  `POST /invocations`) is served and verified inside the arm64 container, but
  nothing is running in AWS.
- **AgentCore Policy: engine created and ACTIVE in a real account.** The five
  Cedar rules are *not* loaded, because AgentCore requires an action-scoped
  policy to name a specific Gateway ARN and no Gateway has been built. Until then
  the same rules are enforced in-process and the UI says so.
- **Live model mode: partially verified.** The intake agent returned a valid
  structured result from `global.anthropic.claude-sonnet-4-6`; the account then
  lost Bedrock access (`INVALID_PAYMENT_INSTRUMENT`), so the full five-node run
  has not completed live.
- **All data is synthetic.** No real report, resident, or organisation appears
  anywhere.
