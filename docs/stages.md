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
| 4 | **Executor contract.** A conformance suite and two different executors that pass it. Spec: `specs/SPEC-0001-executor-contract.md`. Built: specs with front matter, roles, work orders, hand-back validation, re-derived evidence, commit trailers, the recorded executor, the conformance suite and one replayable scenario. Not built: the model gateway, the two live executors, the swap drill and model portability. | Tasks 1 to 6 of 11 built. Tasks 7 to 10 need live models. |
| 5 | **Confidence tower and release.** Mutation score, property-based and held-out tests, fitness functions as blocking gates in real CI. Two-person release for Tier C. Release manifest. Harness-degraded mode. | |
| 6 | **Runtime.** A small agent graph where the regulated figure comes only from the decision service, skills are pinned on the case at start, and the checkpoint and ledger entry share one Postgres transaction. Evals for procedure cards. | |
| 7 | **Change and defect loop.** A production trace raises a defect; reproduce first; a permanent regression test and eval. A skill update that reaches new cases only. | |
| 8 | **Attack pack, complete.** Every claim in both programmes has an attack. Degraded-mode drill. | Started: 36 attacks |

## Why this order

The evidence layer comes first because every later claim rests on it. If a
gate result can be forged or lost, tiers mean nothing. Tiers come second
because they decide what evidence a change needs. Knowledge comes third because
the checks are what the evidence is evidence of. Only then is anything allowed
to generate changes.

## What a peer should be able to do at each stage

Clone, run one command, see the claim hold, then try to make it fail. If a
stage cannot be attacked from a clone, it is not finished.
