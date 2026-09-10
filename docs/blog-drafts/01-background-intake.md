# Agents for Humans: building an agent that interrupts you once

*Draft for builder.aws.com. Publishing is a human action — see `docs/CLAW-BACK.md`.
The current rule asks for "Agents for Humans" in the title; confirm the exact
wording on the Devpost rules tab before posting.*

---

The brief said: an agent that runs quietly in the background and only surfaces
when there's a real decision to make.

That sentence is easy to nod at and hard to build, because almost every design
decision pulls the other way. Chat interfaces surface constantly. Dashboards
surface everything at once and call it visibility. A notification system with no
opinion about what matters is just a faster way to be overwhelmed.

I built Porchlight for a specific person: the part-time volunteer who coordinates
a county elder-fraud response network. Adult protective services, police, a
partner bank, an area agency on aging — and one person with about four hours a
week holding it together. The CFPB says hundreds of US counties have one of
these networks.

Reports arrive one at a time. Each gets handled and filed. Nobody has the hours
to notice that report 7, report 19 and report 44 describe the same crew working
the same postcode.

So the job is not "help the coordinator triage faster". It is: **do the whole
queue unattended, and interrupt once, when reports from different people turn out
to be the same crew.**

Here is what building for that actually forced.

## Accepting a report cannot do any thinking

The webhook returns `202`, not `200`.

```python
@app.post("/reports", status_code=202)
def post_reports(submission: ReportSubmission, _=Depends(_ingest_authorised)):
    result = ingest.accept(submission.to_report())
```

That is not pedantry about HTTP. `200` with an analysis attached would be a
promise the system has not kept — and it would mean the analysis runs inside the
request, which means it does not run when nobody is making requests. A background
agent that only works while someone is watching is a form with extra steps.

`accept()` hashes the artefact, writes a row, and enqueues. Everything expensive
happens in a worker thread that drains the queue on a timer.

## Idempotency is the whole ballgame

If replaying a batch double-counts, every number the coordinator sees is wrong —
including "three residents affected", which is the number they would act on.

The idempotency key is a hash of *(community, reporter, artefact)*, and the queue
enforces it with a `UNIQUE` constraint rather than a check in Python:

```python
def idempotency_key(report: Report) -> str:
    basis = f"{report.community_id}|{report.reporter_pseudonym}|{content_hash(report)}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]
```

Note what is in that key and what is not. **The reporter is in it.** One resident
calling back three times about the same incident is one case. Two residents
describing the same call are two reports — and that is the entire signal the
product exists to find. A dedup rule keyed on content alone would silently delete
the thing I was building.

That distinction is one line of code and it is the difference between a working
product and a broken one. It has a test with a comment saying so.

## Nothing is silently dropped, and nothing is silently guessed

Two failure modes matter more than throughput.

A report that errors is retried a bounded number of times and then marked
`failed` and shown in the UI. Not logged. Shown. A coordinator who believes a
report was handled when it was not is worse off than one who can see it failed.

A report the pipeline could not classify — no script, no indicators, a third-hand
account with nothing in it — goes to `needs_review`, not to a confident green
band. Guessing here is how a real incident gets filed as noise. In a test run
over ten reports, seven vague ones went to review. That is the system declining
to make something up.

## The interruption is one item, and a database constraint keeps it that way

When a campaign first clears threshold, the agent drafts a warning and raises
exactly one decision. A campaign that merely *grew* is not a new ask.

That is enforced by a partial unique index, not by a check the caller might
forget:

```sql
CREATE UNIQUE INDEX ux_decisions_pending
    ON decisions(community_id, campaign_id, kind)
    WHERE state = 'pending' AND campaign_id != '';
```

Two workers racing to escalate the same campaign produce one inbox item. A
coordinator who sees the same ask twice stops reading the inbox, and then the
interruption budget I spent so carefully is gone.

## The screen is a column, not a console

The first version was three panels: queue, campaigns, policy log. It looked
capable. It was the wrong shape, because a three-panel console asks the reader to
decide where to look, and the whole point was to answer that for them.

One column now, and the order of the page is the priority order:

1. what was handled while they were away, written as a sentence because a person
   reads it;
2. the decision, with its evidence and the message already drafted;
3. the case list, ordered by hours until the money cannot be recovered;
4. traces, policy denials and job failures — all folded into `<details>`.

Present, never in the way.

Two details I would defend in a review. Unknown hours-to-loss renders as
"unknown", not as a number; inventing one would put a fabricated figure at the
top of a triage queue. And the drafted resident message is the only serif on the
page, because someone is going to read it aloud to a frightened person and it
should look like prose rather than a form field.

## What it cost to check

The thing I would tell anyone building in this shape: **test it with the
dashboard closed.**

I posted ten reports to the webhook against a real running server, waited for the
worker, then opened the inbox. Ten accepted, three processed into a campaign,
seven routed to review, one decision waiting. Then I replayed the identical
batch: ten duplicates folded, zero new cases, zero duplicate decisions.

That last line is the one I care about. It is also the one that would have been
easy to never check.

---

*Porchlight is Apache-2.0. The evaluation, the challenge set, and an honest
account of what is and is not deployed are in the repo.*
