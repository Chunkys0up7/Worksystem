---
id: SPEC-0006
title: The journey, from discovery to change, as one command
status: approved
cites: [ADR-011, ADR-019, ADR-024, OBL-0001]
allowed_paths:
  - "harness/**"
  - "conformance/**"
  - "knowledge/slices/**"
  - "knowledge/decisions/**"
  - "policy/**"
  - "specs/**"
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

# SPEC-0006: The journey, from discovery to change, as one command

Stage 8 of the reference build. Approved by the owner on 2026-11-10 ("build
this as the full journey").

## Intent

Everything built in stages 1 to 7 is shown as one road, travelled once, in
order: a slice is chosen, a decision becomes a rule and a check, a spec is
approved, an executor builds the change, the tower proves it, two people
release it, cases run on it, something goes wrong, the defect is reproduced and
fixed, and the fix reaches new cases only. Every step is the real mechanism,
not a description of it, and every step lands on one ledger.

Outcome metric: `python -m harness journey` runs from a clean clone with no
credentials and ends with an intact ledger. Number of steps that are narration
without a ledger entry: zero.

## Acceptance criteria

| # | Criterion | Proven by |
| --- | --- | --- |
| J1 | The journey runs end to end and its ledger verifies and reconciles. | `test_journey_runs_and_the_ledger_is_whole` |
| J2 | Every stage writes at least one ledger entry, and the journey names it. The one exception is stated: a spec is approved in git, and its first ledger entry is the work order that cites it. | `test_every_stage_lands_on_the_ledger` |
| J3 | A slice records its outcome metric, obligations and data classes, and its expected tier is derived, before anything is specified. | `test_slice_*` |
| J4 | Two different executors are used on the way (one for the change, one for the fix) and two releases result. | `test_two_executors_two_releases` |
| J5 | The journey says, in its own output, that every model was scripted and what that means. | `test_the_journey_says_what_is_not_live` |
| J6 | Separation of duties is visible in the totals: no identity wrote a fact outside its role. | `test_roles_in_the_journey` |
