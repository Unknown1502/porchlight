# System prompts

Every prompt Porchlight uses, in full. This file is **generated from
`src/porchlight/prompts/`** by `make docs` — the code is the source of truth, so
these can never drift out of sync with what actually ships.

## The two rules that govern all of them

**1. Untrusted content never enters a system prompt.** Reported artefacts are
written by fraudsters. They travel on the user turn, inside an envelope, and
every system prompt tells the model that whatever is inside is evidence to
describe, never instructions to follow.

```python
UNTRUSTED_OPEN  = "<<<REPORTED_ARTEFACT id={rid}>>>"
UNTRUSTED_CLOSE = "<<<END_REPORTED_ARTEFACT>>>"

def envelope(report_id: str, content: str) -> str:
    # A forged closing delimiter inside the content is neutralised, not silently
    # honoured - otherwise an attacker ends the envelope early and the rest of
    # their text reads as trusted.
    safe = content.replace(UNTRUSTED_CLOSE, "[REDACTED-FORGED-DELIMITER]")
    return f"{UNTRUSTED_OPEN.format(rid=report_id)}\n{safe}\n{UNTRUSTED_CLOSE}"
```

**2. Prompts carry no authority.** No prompt here can authorise a side effect.
Sending, publishing and contacting are decided by Cedar policy at the gateway,
outside the model's reach. If an injection succeeds at the language level, it
still cannot reach a tool. That is the difference between a control and a
request, and it is the argument the whole submission rests on.

## Prompt inventory

| Agent | Tools | Structured output | Section |
|---|---|---|---|
| `intake` | none | `IntakeResult` | [Intake](#intake) |
| `corroboration` | 4 allow-listed | `CorroborationResult` | [Corroboration](#corroboration) |
| `script_matcher` | none | `StageAssessment` | [Script matcher](#script-matcher) |
| `money_rail` | none | `StageAssessment` | [Money rail](#money-rail) |
| `isolation` | none | `StageAssessment` | [Isolation](#isolation) |
| `campaign` | `find_candidate_cluster` | `CampaignResult` | [Campaign](#campaign) |
| `response` | none | `ResponseResult` | [Response](#response) |

---

## Shared blocks

Composed into the prompts below. Editing one changes every agent, on purpose -
the adversarial-input contract must be identical everywhere.

### ADVERSARIAL_INPUT_RULE

```text
__ADV__
```

### PRIVACY_RULE

```text
__PRIV__
```

### MISSION

```text
__MISSION__
```

### OUTPUT_RULE

```text
__OUT__
```

---
