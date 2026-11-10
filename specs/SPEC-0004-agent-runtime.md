---
id: SPEC-0004
title: The agent runtime, pinned versions and the decision receipt
status: approved
cites: [ADR-027, ADR-030, ADR-024, ADR-025, OBL-0001]
allowed_paths:
  - "runtime/**"
  - "harness/**"
  - "policy/**"
  - "knowledge/decisions/**"
  - "knowledge/skills/**"
  - "specs/**"
  - "conformance/**"
  - "executors/**"
  - "attacks/**"
  - "tests/**"
  - "docs/**"
  - "tools/**"
  - "demo-keys/**"
  - ".github/workflows/**"
  - "README.md"
  - "CLAUDE.md"
  - "Makefile"
---

# SPEC-0004: The agent runtime, pinned versions and the decision receipt

Stage 6 of the reference build. Approved by the owner on 2026-11-10 ("build
this as the full journey"). This spec may not touch `services/`. It re-releases
the skill once, as an SME change in its own commit, to name the eval that
checks PROC-0001.

## Intent

A released decision service is used by a running case in a way that can be
reconstructed later: which version, which rules, who decided, and that no agent
produced the figure.

Outcome metric: for every completed synthetic case, the ledger alone answers
which version decided it, which cards applied and whether a person was
involved. Number of regulated figures produced by an agent: zero.

## Acceptance criteria

| # | Criterion | Proven by |
| --- | --- | --- |
| R1 | Each step is a checkpoint written in the same database transaction as its ledger entries. A crash between them leaves neither. | `test_checkpoint_and_ledger_commit_together` |
| R2 | Checkpoints and ledger entries reconcile one to one. | `test_runtime_reconciles` |
| R3 | A regulated figure comes only from the decision service: only the calculate node may write the result, and a node that writes outside its contract halts the case. | `test_only_the_decision_service_decides` |
| R4 | Anything an agent says about the figure is checked against the receipt; a draft that states another amount is discarded and the trip recorded. | `test_explanation_guard` |
| R5 | A case uses the version pinned when it started. A newer release applies to new cases only. A case moves version only by an approved, recorded migration. | `test_pinning_*` |
| R6 | A stored release that differs from its sealed manifest is not run. | `test_tampered_release_is_not_run` |
| R7 | PROC-0001: conflicting documents route to a person and are not calculated. An instruction inside a document does not change that. | `test_proc_0001_*`, the eval |
| R8 | A routed case is durable: a new engine instance resumes it. Only a case reviewer can resolve it. | `test_human_review_is_durable` |
| R9 | Personal data is in the vault by reference. After erasure the ledger and checkpoints are intact and hold none of it. | `test_erasure_*` |
| R10 | A model sees a class of data only if its pin approves it. | `test_data_path_register` |
| R11 | The eval result closes the PROC-0001 link in the ID chain. | `test_eval_closes_the_procedure_link` |

## Out of scope

A real orchestration product behind the node contract. A live model. Shadow and
canary exposure. Fairness monitoring. Multi-agent hand-offs. Retirement of an
agent. A user interface for the reviewer. These are named in `docs/limits.md`.
