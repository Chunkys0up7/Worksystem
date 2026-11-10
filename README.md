# Agentic delivery lifecycle: a reference build

A working reference for one idea: **an agentic delivery lifecycle gets shorter
only when review follows risk, and review can follow risk only when the
evidence is trustworthy.** Generating code is already the cheap part.

This repository is the idea made runnable, from discovery through build,
release and production to a defect and its fix. Clone it, run it, and try to
break it. Everything deterministic is real and tested. Nothing depends on a
particular model, agent product or vendor.

**No live model has run in this repository.** Every model is scripted: a small
deterministic program stands in for it, and every line it touches says so. What
is real is everything around the model: the gates, the evidence, the releases,
the runtime. Read `docs/limits.md` before drawing any conclusion about agents.

The frame is a federally supervised, heavily regulated financial institution.
The service slice (income verification for a consumer loan) and every rule in
it are synthetic.

## Run it

```
pip install pyyaml cryptography pytest hypothesis
python -m harness ui          # the console: show and trigger all of it from a browser
python -m harness journey     # the whole road, discovery to change, on one ledger
python -m harness attacks     # 76 scripted attempts to break the claims
python -m pytest -q           # 360 tests, including property-based tests
```

Python 3.11 or later. No services to start and no credentials: the ledger
defaults to SQLite. Set `LEDGER_PG_DSN=postgresql://...` to run the ledger
tests against Postgres as well.

## The console

`python -m harness ui` serves one page at `http://127.0.0.1:8765/`. From it you
can travel the journey step by step, run any attack, read the ledger the
journey wrote, edit an entry the way a database owner could and see what
verification says, and tier any path. Every button calls the function the
command line calls. The page asks no other server for anything.

`python -m harness ui --export run.html` runs everything once and writes a
self-contained page with the results in it, labelled as a recording.

The console is presentation. It holds no control and it has no login: see
`docs/limits.md`, section 7j.

## The journey

`python -m harness journey` travels the road once, in about forty seconds.
Each step runs the real mechanism and lands on one ledger.

| Step | What happens | What it shows |
| --- | --- | --- |
| Discover | A slice is chosen, with its outcome metric and what makes it regulated. | Tier, obligations and data classes are known before anything is specified. |
| Decide | A product decision becomes a rule card, a paired check and a signed skill. | Knowledge reaches agents as owned, versioned skills. The check is red. |
| Specify | A spec is approved: which decision, which paths. | Work starts from a spec. It states no tier. |
| Build | An executor gets a private worktree and reaches a model only through the gateway. | The harness derives what changed and trusts none of the report. |
| Prove | Fitness functions, held-out cases, a mutation score. A second model advises. | No number, no merge. Advice cannot pass or block. |
| Release | The launcher's approval is refused. Two other people release. | Tier C is two people, bound to the exact gate result. A manifest is sealed. |
| Run | Cases run on the release. One is routed to a person. | The figure comes only from the decision service, at the pinned version. |
| Observe | One case halts. Another completes with a wrong input accepted. | A real defect in the synthetic service, and the half of it nothing noticed. |
| Reproduce | A fix is attempted and blocked. The defect is reproduced against the release. | Reproduce before fixing. |
| Fix | A different executor builds the fix. Same gates, two people, a second release. | The executor is a contract. The fix is a change like any other. |
| Change | The waiting case finishes on the old version. A new case gets the fix. | A release reaches new cases only. |
| Account | Personal data is erased. The ledger verifies and reconciles. | Sixteen roles wrote to it, none outside its role. Then: what this did not show. |

Smaller pieces can be run alone:

```
python -m harness demo                             # one pipeline, three changes, three tiers
python -m harness conformance loop --show-run      # one work order through the executor contract
python -m harness chain PD-0001                    # decision -> skill -> checks -> ledger
python -m harness why services/decision_income/calc.py
python -m harness spec                             # the spec for each stage
```

## Try to break it

`python -m harness attacks` runs each attack in a throwaway copy and reports
HELD, BROKEN or LIMIT. Five rows are LIMIT on purpose: attacks this build does
not stop, with the control that would. Add your own in `attacks/pack.py`.

Some to try by hand:

```
# Can a change hide a regulated path?
python -m harness tier --paths docs/a.md docs/../services/decision_income/calc.py

# What tier is a path nobody mapped?
python -m harness tier --paths some/new/place.py

# Change a rule in the code and see what CI says.
python -m harness demo
sed -i 's/VARIABLE_HISTORY_MIN_MONTHS = 24/VARIABLE_HISTORY_MIN_MONTHS = 12/' services/decision_income/calc.py
python -m harness change CHG-try --paths services/decision_income/calc.py
git checkout services/decision_income/calc.py
```

Point the retro-tier report at your own repository, with your own path map,
to get the number that matters most (see `docs/limits.md`):

```
python -m harness retro --repo /path/to/repo --since "3 months ago"
```

## How to read the repository

| Where | What |
| --- | --- |
| `docs/interrogation-map.md` | Every claim, where it is proven, how to attack it. Start here. |
| `docs/limits.md` | What this build does not prove. Read this second. |
| `docs/stages.md` | The nine stages and what each left unbuilt. |
| `specs/` | One spec per stage. Work starts from a spec; every commit cites one. |
| `policy/` | The governance plane as data: roles, tier rules, path map, skill map, deny list, model pins, tower thresholds, records schedule. |
| `knowledge/` | Blueprint, slice, decisions, cards, and one signed skill with its paired checks. |
| `services/decision_income/` | A deterministic decision service. No model in the path. |
| `harness/` | Programme 1, the delivery harness: ledger, tier rulebook, work orders, gateway, tower, release, defect loop. |
| `runtime/` | Programme 2, the agent runtime: case engine, node contract, vault, evals, synthetic cases. |
| `executors/` | Executor adapters and the scripted models. No control lives here. |
| `conformance/` | The executor conformance suite, two replayable scenarios, the journey. |
| `heldout/` | Cases no executor ever sees. |
| `ui/` | The console: one page and a loopback server. Presentation only. |
| `attacks/` | The attack pack. |
| `tests/` | Contract, property and integration tests. |

## What is and is not here

Built, across nine stages: an evidence layer, a tier rulebook, knowledge as
signed skills, an executor contract with three executors, a confidence tower,
two-person release, an agent runtime with pinned versions, a defect loop, one
journey over all of it, and a console to show it. 360 tests. 76 attacks: 71
held, 5 stated limits.

Not here, and the build says so wherever it matters: a live model, an
operating-system sandbox, key custody, an object-locked anchor, a real pipeline
with branch protection, Tier A auto-merge, and any measurement of whether the
lifecycle gets shorter. `docs/limits.md` has the full list.

`demo-keys/` holds private signing keys, committed deliberately so the build
runs from a clone. They protect nothing. See `demo-keys/README.md`.
