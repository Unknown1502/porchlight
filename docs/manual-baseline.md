# The manual baseline: not measured

**Status: NOT MEASURED. No time-saving claim is made anywhere in this project.**

## What was here before

An earlier version of `eval/run_eval.py` contained:

```python
# Measured by timing a coordinator doing this by hand for one report:
# retype notes, four lookups, fill the portal form, email two partners.
MANUAL_BASELINE_SECONDS = 18 * 60
```

and divided it by the pipeline's own latency to produce a `speedup` field, which
in offline mode evaluated to roughly **254,000×**.

Both halves were unsupported:

- **The baseline.** No timing data, session record, participant, or procedure
  exists anywhere in this repository. The comment asserted a measurement that was
  never taken. Eighteen minutes may well be plausible; plausible is not measured.
- **The divisor.** The denominator was the *offline deterministic stub's* runtime
  — a few milliseconds of regex matching, not the deployed system's latency. Even
  with a real baseline, dividing by it produces a number about the stub, not
  about Porchlight.

A caveat string sat next to the figure warning readers not to cite it. A number
that needs a warning label not to mislead is a number that should not be
computed. Both are removed.

## What would make a real one

If someone wants this number, here is a procedure that would produce a defensible
one. It has **not** been run.

### Setup

1. Recruit 3–5 people who actually do coalition intake. Not the builder, and not
   anyone who has seen this repository — familiarity with the tool is the single
   largest confound.
2. Draw 10 reports from `corpus/seed/reports.json`, stratified so the mix of
   urgency bands matches the corpus. Give every participant the same 10, in a
   different order per participant (counterbalanced, to separate learning effects
   from task difficulty).
3. Give participants their own normal tools and no access to Porchlight.

### Task, per report

Define "done" precisely and identically for both arms, because an unequal
definition of done is where this kind of comparison usually goes wrong. For each
report the participant must produce:

- the structured fields in `IntakeResult` (impersonated entity, pretext, money
  rail, amount, deadline, indicators);
- an urgency band with a one-line justification;
- an explicit yes/no on whether it relates to any earlier report in the set, with
  the linking evidence named;
- a completed complaint draft for the jurisdiction pack in use.

### Measurement

- Time each report individually, from opening it to declaring it done. Record
  per-report times, not a total; the distribution matters more than the mean, and
  a median is the honest summary for a small sample.
- Record interruptions and exclude that time, or don't, but say which.
- Have a second person score each output against ground truth in
  `eval/labels.json`. **Report accuracy alongside time.** A system that is faster
  and wrong is not faster.

### Comparison

- Run Porchlight's arm with `PORCHLIGHT_OFFLINE=0`, so the comparison is against
  the deployed path rather than the stubs.
- Include the coordinator's review-and-approve time in Porchlight's arm. The human
  decision boundary is deliberate; excluding the human from the timing would be
  measuring a system nobody is proposing to run.
- Report the difference with the sample size and the spread. With five
  participants, that is a small-sample observation, and it should be labelled as
  one rather than promoted to a headline ratio.

### What to report

A sentence of the form:

> Median time per report, n=5 coordinators, 10 reports each: manual X min
> (IQR a–b), Porchlight-assisted Y min (IQR c–d), at equal or better accuracy
> against the labelled corpus.

Not a multiplier.

## Until then

`eval/run_eval.py` reports per-report latency as a raw number, labelled with the
mode it ran in, and makes no comparison. The README claims no time saving.
