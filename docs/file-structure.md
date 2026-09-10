# File structure

Status column: **✅ built and passing tests** · **◻ specified, not yet written**

```
porchlight/
├── README.md                              ✅  build spec, quickstart, results
├── LICENSE                                ✅  Apache-2.0 (hard submission requirement)
├── pyproject.toml / requirements*.txt     ✅
├── Makefile                               ✅  install · corpus · demo · eval · test · injection
├── .env.example                           ✅
│
├── docs/
│   ├── architecture.md                    ✅  node graph, mermaid diagram, data flow
│   ├── file-structure.md                  ✅  this file
│   ├── manual-baseline.md                 ✅  why there is no time-saving claim
│   ├── prompts.md                         ✅  every system prompt, annotated
│   ├── threat-model.md                    ✅  adversarial input model
│   ├── demo-script.md                     ✅  the five minutes, beat by beat
│   └── build-plan.md                      ✅  what is left, in order
│
├── packs/                                 ✅  jurisdiction as config, not a code fork
│   ├── in.yaml                                NCRP / 1930, partners, script library
│   └── us.yaml                                FTC ReportFraud / IC3, APS, AAA
│
├── policies/
│   ├── README.md                          ✅  why two implementations exist
│   ├── cedar/P001…P005.cedar              ✅  the deployed rules
│   └── natural_language/policies.md       ✅  the same five rules in English
│
├── src/porchlight/
│   ├── config.py                          ✅  Settings + jurisdiction pack loader
│   ├── models.py                          ✅  every structured-output schema
│   ├── policy.py                          ✅  in-process Cedar-equivalent shim
│   ├── memory.py                          ✅  AgentCore Memory, community-scoped
│   ├── correlation.py                     ✅  deterministic union-find clustering
│   ├── pipeline.py                        ✅  orchestrator, both modes
│   ├── offline.py                         ✅  deterministic nodes for CI/dev
│   ├── cli.py                             ✅  replay · one · reset · setup-memory
│   ├── prompts/
│   │   ├── _shared.py                     ✅  adversarial-input + privacy blocks
│   │   ├── intake.py                      ✅
│   │   ├── corroboration.py               ✅
│   │   ├── stage_swarm.py                 ✅  3 specialists + task template
│   │   ├── campaign.py                    ✅
│   │   └── response.py                    ✅
│   ├── agents/factory.py                  ✅  one place where capability is declared
│   ├── tools/
│   │   ├── sanitize.py                    ✅  scrub_pii + detect_injection
│   │   ├── indicators.py                  ✅  regex extraction + clustering keys
│   │   ├── enrichment.py                  ✅  RDAP · URLhaus · phone shape
│   │   └── store.py                        ✅  community report store
│   ├── server.py                          ✅  FastAPI: POST /report, GET /queue,
│   │                                          GET /campaigns, POST /approve,
│   │                                          + /audit /case /reset /replay and the
│   │                                          AgentCore contract (/invocations, /ping)
│   └── dashboard/index.html               ✅  coordinator queue, single file, no build
│
├── corpus/
│   ├── templates.yaml                     ✅  8 reconstructed scripts, staged
│   └── generate.py                        ✅  60 reports, planted campaign, decoys
│
├── eval/
│   ├── run_eval.py                        ✅  P/R/F1 + gates, exits non-zero on fail
│   └── labels.json                        ✅  ground truth (generated)
│
├── tests/                                 ✅  76 passing
│   ├── test_indicators.py                     extraction + PII scrubbing
│   ├── test_server.py                         endpoints, queue order, approval gate
│   ├── test_correlation.py                    the false-campaign guards
│   ├── test_policy_parity.py                  Cedar ↔ shim must not drift
│   └── injection/
│       ├── cases.yaml                         20 payloads, 6 attack categories
│       └── test_injection.py                  asserts no side effect, not "model refused"
│
├── deploy/
│   ├── Dockerfile                         ✅  ARM64, AgentCore Runtime contract
│   ├── deploy_runtime.sh                  ✅  preflight, then configure && launch
│   └── setup_policy.sh                    ✅  preflight + cedar load for the engine
│
└── .github/workflows/ci.yml               ✅  ruff + pytest + eval gate, offline mode
```

## Notes on the pieces that were built last

**`server.py`** — the four endpoints from the spec, plus the three the dashboard
needs to render its other two panels (`/audit`, `/case/{id}`) and to reset and
warm itself between takes (`/reset`, `/replay`). It carries no judgement of its
own: every decision still comes from `pipeline.py` and `policy.py`. The one
exception is deliberate — `POST /approve` is the only place in the system that
mints an approval token, and it mints it from a named human. That is what P002
checks for, and it is why an approval can never arrive as *data* inside a
report. `tests/test_server.py` asserts exactly that.

**`dashboard/index.html`** — one file, no build step, no CDN, so it can be
served from the same container as the API and cannot be broken by a network
hiccup mid-demo. Queue on the left ordered by hours-to-loss, campaign panel
top-right, policy audit log bottom-right with a **Denied only** filter — that
filter is what you press before the injection beat instead of scrolling.

One design rule runs through it: saturated colour means severity and nothing
else. Brand warmth is confined to the porch-light glow and the logo; controls
are bone-white. So when something on that screen is red, it is red because a
person is hours from losing money. Colour is never the only carrier — every
band is also a word, every denial is also the string `FORBID`.

**`/replay` and the `exclude` list** — the demo's setup step. Holding back
`rpt-0003`…`rpt-0006` leaves the planted campaign one report short of the
threshold, so pasting one of them live is what tips it over and the panel fires
on camera rather than being there when the page loads.

**`deploy/`** — `deploy_runtime.sh` is mostly preflight, on purpose: it refuses
to deploy with `PORCHLIGHT_OFFLINE=1`, because that failure otherwise shows up
as a container that builds, launches, and quietly serves deterministic stubs.
`setup_policy.sh` discovers the AgentCore Policy operations from the installed
CLI rather than hard-coding them, and stops with instructions if they are
absent — the service is new, and a plausible-looking wrong command against
someone's account is worse than no command.

**`ci.yml`** — `ruff check`, `pytest`, the red-team suite called out separately,
then a corpus regeneration and `eval/run_eval.py`. The eval exits non-zero below
attribution F1 0.80 or injection recall 0.90, so a clustering regression breaks
the build rather than the demo. The ruff rule set is pinned in `pyproject.toml`
so a ruff release cannot fail the build on its own.
