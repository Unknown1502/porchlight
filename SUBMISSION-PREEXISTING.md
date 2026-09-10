# Pre-existing work disclosure

Required by the hackathon rule that a submitted project be newly created during
the submission period, with any incorporated pre-existing work disclosed.

This file records what the repository can actually evidence, and — separately and
explicitly — what it cannot. The distinction matters: a disclosure that quietly
presents an assumption as a verified fact is worse than no disclosure.

---

## 1. What the repository evidences

**Version control does not cover the initial development.** `git init` was run on
2026-09-10, part-way through the project, during a code-audit session. The
repository therefore contains two commits, both dated 2026-09-10, and neither of
them documents when the code inside them was written.

```
9745eb9  2026-09-10  Regenerate corpus labels from the final seeded run
8c70cef  2026-09-10  Audit pass: verified baseline, closed real gaps, ...
```

**Filesystem timestamps are the only in-repo provenance signal**, and they are
weak evidence — mtimes are trivially altered and are not a chain of custody. For
what it is worth, they are internally consistent and span two days:

| | Timestamp |
|---|---|
| Earliest source file | 2026-09-09 17:50 |
| Latest source file | 2026-09-10 11:33 |

**No prior version control, vendored code, or third-party source tree is present.**
`.git` is the only VCS directory and was created during this project. There is no
`vendor/`, `third_party/`, or `.orig` file anywhere in the tree.

## 2. Third-party dependencies

All incorporated third-party code enters through declared package dependencies,
not by copying source. Pinned in `requirements.txt` / `requirements-dev.txt`:

| Package | Version | Role |
|---|---|---|
| `strands-agents` | 1.55.0 | Agent framework (required by the hackathon) |
| `strands-agents-tools` | 0.8.8 | Tool decorators |
| `bedrock-agentcore` | 1.22.0 | AgentCore SDK |
| `bedrock-agentcore-starter-toolkit` | 0.3.12 | Deployment CLI |
| `fastapi`, `uvicorn`, `pydantic`, `httpx`, `pyyaml`, `python-dotenv`, `rapidfuzz` | see requirements | Web layer, schemas, HTTP, config, fuzzy matching |

No source from any of these is copied into this repository.

## 3. Content provenance

- **The corpus is synthetic.** Scam scripts in `corpus/templates.yaml` are
  reconstructions of publicly documented patterns. No real report, message,
  victim, or organisation is reproduced. Domains use the reserved `.example` TLD
  (RFC 2606); phone numbers, payment handles and wallet addresses are invented
  placeholders held constant for reproducibility.
- **Jurisdiction packs** (`packs/in.yaml`, `packs/us.yaml`) cite public reporting
  endpoints (national portals, helplines). These are public reference data, not
  a partnership or endorsement, and no organisation named in them has reviewed or
  approved this project.
- **External statistics** in the README are cited inline to the FTC and CFPB.
  They are context for the problem, not measurements of this system.

## 4. What only the author can confirm

The following cannot be established from the repository and are recorded here as
**author attestations**, to be confirmed by the author before submission. They
are deliberately not asserted as verified facts.

- [ ] **The submission period opened on or before 2026-09-09**, so that work
      beginning that day falls inside it. The public Devpost overview page states
      the deadline (2026-09-14, 5:00pm PDT) but does not publish an opening date;
      confirm against the official rules page.
- [ ] **No code, design, or written material in this repository was carried in
      from an earlier project** of the author's or anyone else's.
- [ ] **No portion was developed before the submission period opened.**

If any box above cannot be ticked truthfully, the affected work must be described
here specifically — what it is, where it came from, and when it was created —
before the project is submitted.

## 5. AI assistance

Development was assisted by an AI coding agent (Claude). It was used for
implementation, code review, and documentation under the author's direction. This
is disclosed for completeness; it is not third-party code incorporation, and all
of it was written for this project.

---

*Last updated: 2026-09-10. Update this file if any answer in §4 changes.*
