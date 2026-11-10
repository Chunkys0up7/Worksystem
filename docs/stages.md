# Build stages

One synthetic service slice runs through every stage. Each stage is runnable
alone and adds rows to `docs/interrogation-map.md` and attacks to the pack.

The rule for every stage: anything deterministic is real; model calls may be
recorded or stubbed, and are labelled when they are.

| Stage | What | Status |
| --- | --- | --- |
| 1 | **Spine.** Repository layout by release unit, ID scheme, workload identities, the ledger API (signed, hash-chained, anchored, fails closed), records schedule as code, completeness reconciliation. SQLite and Postgres. | Built |
| 2 | **Tier rulebook.** Deterministic tiers from path map, data classes, obligations and skill map. Retro-tier report over git history. | Built |
| 3 | **Knowledge.** Service blueprint slice, Fact, Rule, Calculation and Procedure cards, a decision register, one SME-owned skill, signed and content-hashed, with paired deterministic checks. The ID chain walked both ways. | Built |
| 4 | **Executor contract.** Specs, roles, work orders, hand-back validation, re-derived evidence, commit trailers, the model gateway (pins, roles, budget), the tool box (deny before the action), three executors (recorded, tool loop, command-line), advisory review, the swap drill and the conformance suite, on one replayable scenario. Spec: `specs/SPEC-0001-executor-contract.md`. | Built, with scripted models. No live model has run. |
| 5 | **Confidence tower and release.** Fitness functions, held-out cases and mutation score as blocking gates by tier. Two-person release for Tier C bound to the gate result. Sealed release manifest and a versioned release store. Harness-degraded mode. Spec: `specs/SPEC-0003-confidence-tower-and-release.md`. | Built. Not built: Tier A auto-merge, promotion from git, canary and rollback. |
| 6 | **Runtime.** A small case engine with a node contract. The regulated figure comes only from the decision service at the version pinned on the case. Checkpoint and ledger entries in one transaction, on SQLite and Postgres. Personal data by reference in an erasable vault. Human review that survives a restart. An eval with adversarial twins for the procedure card. Spec: `specs/SPEC-0004-agent-runtime.md`. | Built, with scripted models. Not built: a real orchestration product, shadow and canary, fairness monitoring. |
| 7 | **Change and defect loop.** A production trace raises a defect; reproduce first; a permanent regression test and eval. A skill update that reaches new cases only. | |
| 8 | **Attack pack, complete.** Every claim in both programmes has an attack. Degraded-mode drill. | Started: 68 attacks |

## Why this order

The evidence layer comes first because every later claim rests on it. If a
gate result can be forged or lost, tiers mean nothing. Tiers come second
because they decide what evidence a change needs. Knowledge comes third because
the checks are what the evidence is evidence of. Only then is anything allowed
to generate changes.

## What a peer should be able to do at each stage

Clone, run one command, see the claim hold, then try to make it fail. If a
stage cannot be attacked from a clone, it is not finished.
