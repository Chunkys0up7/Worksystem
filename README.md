# Agentic delivery lifecycle: a reference build

A working reference for one idea: **an agentic delivery lifecycle gets shorter
only when review follows risk, and review can follow risk only when the
evidence is trustworthy.** Generating code is already the cheap part.

This repository is the idea made runnable. Clone it, run it, and try to break
it. Everything deterministic is real and tested. Nothing here depends on a
particular model, agent SDK or vendor.

The frame is a federally supervised, heavily regulated financial institution.
The service slice (income verification for a consumer loan) and every rule in
it are synthetic.

## Run it

```
pip install pyyaml cryptography pytest hypothesis
python -m harness demo                             # one pipeline, three changes, three tiers
python -m harness conformance loop --show-run      # a work order through the executor contract
python -m harness attacks                          # 55 scripted attempts to break the claims
python -m pytest -q                                # 256 tests, including property-based tests
```

Python 3.11 or later. No services to start: the ledger defaults to SQLite.
Set `LEDGER_DSN=postgresql://...` to run on Postgres, and
`LEDGER_PG_DSN=postgresql://...` to run the ledger tests against both.

## What you will see

`python -m harness demo` sends three changes through the same pipeline:

| Change | Touches | Tier | What the gate asks for | Outcome |
| --- | --- | --- | --- | --- |
| CHG-demo-a | a documentation file | A | tier recorded | passed |
| CHG-demo-b | harness code and a test | B | tier recorded | passed |
| CHG-demo-c | the regulated calculation | C | four paired checks, re-run by CI, then two people | held for approval |

Each step is written to the ledger by a different identity. The executor
proposes. CI sets the tier from the paths. CI re-runs the checks. CI records
the gate. Then the demo walks the ID chain both ways:

```
python -m harness chain PD-0001                           # decision -> skill -> checks -> ledger
python -m harness why services/decision_income/calc.py    # code -> skill -> cards -> decisions
```

`python -m harness conformance loop --show-run` goes one step further. An SME
has added a rule and its paired check, so the check is red. An executor is
handed a work order: a private copy of the repository, the paths its spec
allows, and the released skill. It reaches a model only through the gateway and
touches files only through the harness's tool box. The harness then trusts
nothing the executor says. It derives what changed, rejects anything out of
scope, re-runs the checks from the released skill, records where the executor's
claim differs, takes advice from a second model without letting it decide
anything, and closes the chain from decision to spec to change to evidence.

**The model in that run is scripted**: a deterministic program stands in for it
and the output says so on every line it touches. Three executors that share no
code path (`recorded`, `loop`, `cli-stub`) leave identical control records. No
live model has run in this repository. The contract is in
`specs/SPEC-0001-executor-contract.md`.

## Try to break it

`python -m harness attacks` runs each attack in a throwaway copy and reports
HELD, BROKEN or LIMIT. Four rows are LIMIT on purpose: attacks this build does
not stop, with the control that would. Add your own in `attacks/pack.py`.

Some to try by hand:

```
# Can a change hide a regulated path?
python -m harness tier --paths docs/a.md docs/../services/decision_income/calc.py

# What tier is a path nobody mapped?
python -m harness tier --paths some/new/place.py

# Change a rule in the code and see what CI says.
sed -i 's/VARIABLE_HISTORY_MIN_MONTHS = 24/VARIABLE_HISTORY_MIN_MONTHS = 12/' services/decision_income/calc.py
python -m harness change CHG-try --paths services/decision_income/calc.py
git checkout services/decision_income/calc.py

# Edit the ledger behind the API, then verify it.
python -m harness ledger verify
```

Point the retro-tier report at your own repository, with your own path map,
to get the number that matters most (see docs/limits.md):

```
python -m harness retro --repo /path/to/repo --since "3 months ago"
```

## How to read the repository

| Where | What |
| --- | --- |
| `docs/interrogation-map.md` | Every claim, where it is proven, how to attack it. Start here. |
| `docs/limits.md` | What this build does not prove. Read this second. |
| `docs/stages.md` | What is built, what is next, in what order. |
| `specs/` | The spec for the next stage. Work starts from a spec. |
| `policy/` | The governance plane as data: identities, tier rules, path map, skill map, records schedule. |
| `knowledge/` | Blueprint, decisions, cards, and one signed skill with its paired checks. |
| `services/decision_income/` | A deterministic decision service. No model in the path. |
| `harness/` | The delivery harness: ledger, tier rulebook, gates, checks, work orders, hand-back validation, ID chain. |
| `executors/` | Executor adapters (recorded, tool loop, command-line) and the scripted models. No control lives here. |
| `conformance/` | The executor conformance suite and a replayable scenario. |
| `attacks/` | The attack pack. |
| `tests/` | Contract, property and integration tests. |

## Status

Stages 1 to 3 of 8 are built: the evidence spine, the tier rulebook and the
knowledge slice. Stage 4, the executor contract, is half built: everything
that needs no model (specs, roles, work orders, hand-back validation, the
recorded executor, the conformance suite). **No model call has run in this
repository.** The model gateway, two live executors and the swap drill are
next. See `docs/stages.md`.

`demo-keys/` holds private signing keys, committed deliberately so the build
runs from a clone. They protect nothing. See `demo-keys/README.md`.
