---
id: SPEC-0003
title: The confidence tower, two-person release and degraded mode
status: approved
cites: [ADR-013, ADR-020, ADR-024, OBL-0001]
allowed_paths:
  - "harness/**"
  - "policy/**"
  - "knowledge/decisions/**"
  - "specs/**"
  - "heldout/**"
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

# SPEC-0003: The confidence tower, two-person release and degraded mode

Stage 5 of the reference build. Approved by the owner on 2026-11-10 ("build
this as the full journey"). This spec may not touch `services/` or the released
skill.

## Intent

A change merges on numbers, and a Tier C change is released by two people who
did not launch it. When the pipeline is down there is a defined path, not an
improvised one.

Outcome metric: for the scenario change, every gate its tier requires has a
number in the ledger, and the release manifest names the evidence, the
approvals and the versions. Number of releases possible without two approvers
at Tier C: zero.

## Acceptance criteria

| # | Criterion | Proven by |
| --- | --- | --- |
| B1 | Each tier has a stated set of gates, and each gate records a number and a threshold. | `test_tower_gates_by_tier` |
| B2 | A gate with no number blocks. | `test_no_number_no_merge` |
| B3 | Fitness functions block a decision service that uses floating point or imports beyond its allow-list, and a harness that imports an adapter. | `test_fitness_*`, attacks |
| B4 | Held-out cases are never in a worktree, and a change that fits the visible checks without implementing the rule is blocked. | `test_heldout_*`, attack |
| B5 | The mutation gate blocks a change whose code the checks do not exercise. | `test_mutation_*`, attack |
| B6 | Tier C release needs two approvals from two different people, neither the launcher, each bound to the gate result being released. | `test_release_*`, attacks |
| B7 | No executor, reviewer or CI identity can write an approval. | `test_only_approvers_approve` |
| B8 | A release seals a manifest and stores the released files by version; a stored release that differs from its manifest is detected. | `test_manifest_*` |
| B9 | Degraded mode: fixed gates, two signers, spool, back-fill, late flag, overdue report. Nothing is waived. | `test_degraded_*` |

## Out of scope

Tier A auto-merge with sampling. Branch protection and promotion from git by a
real pipeline (the release reads the validated worktree). Canary and rollback.
Misclassification sampling. These are named in `docs/limits.md`.
