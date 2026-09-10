
## Tuning notes

Things that moved the numbers, recorded so they are not re-litigated at 2am:

- **`script_fingerprint` is the load-bearing field.** Campaign correlation is
  only as good as fingerprint stability. The prompt gives worked examples of good
  and bad fingerprints because free-form labels drift, and drifted labels break
  clustering silently rather than loudly.
- **Corroboration confidence rates *external* corroboration, not scam
  likelihood.** Conflating the two produced uniformly high confidence and a
  useless field. The prompt now says so explicitly and defines all four levels.
- **The urgency rubric had to be about time, not severity.** "Is this a scam" is
  already answered by everything reaching the queue. "How many hours until this
  is irreversible" is the only question that orders a queue usefully.
- **`decision_for_human` is the brief's contract.** The hackathon asks for an
  agent that "only surfaces when there's a real decision to make". That field is
  that clause, which is why the prompt bans status updates in it.
- **The response prompt forbids unverified deadline claims.** A confidently wrong
  freeze window printed on a flyer for older people is worse than no flyer.
