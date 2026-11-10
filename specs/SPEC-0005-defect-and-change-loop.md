---
id: SPEC-0005
title: The defect and change loop
status: approved
cites: [ADR-015, ADR-013, ADR-027, OBL-0001]
allowed_paths:
  - "harness/**"
  - "runtime/**"
  - "policy/**"
  - "knowledge/decisions/**"
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

# SPEC-0005: The defect and change loop

Stage 7 of the reference build. Approved by the owner on 2026-11-10 ("build
this as the full journey"). This spec may not touch `services/`, the cards or
the released skill on main. The defect and its fix live in a scenario.

## Intent

Something goes wrong in production and the way back to a fix is the same road
as any other change, with one extra rule at the start: reproduce it first.

Outcome metric: a defect seen in a running case becomes a released fix with a
permanent regression check, and every step is on the ledger. Number of fixes
started before the defect was reproduced: zero.

## The defect used

A real one, found in this repository's synthetic decision service: it fails
with an arithmetic error on an amount that is not a number or is infinite, and
in one path it accepts an infinite amount and returns a result. It was not
planted. It was found by asking what the service does with inputs no check
covered.

## Acceptance criteria

| # | Criterion | Proven by |
| --- | --- | --- |
| D1 | A defect cites ledger entries that exist, and carries no personal data. | `test_defect_cites_the_ledger` |
| D2 | Reproduction runs the regression check and held-out cases against the released version, under the gates role, and records whether they fail. | `test_reproduction_against_the_release` |
| D3 | A spec that fixes a defect gets no work until the defect is reproduced. | `test_no_fix_before_reproduction` |
| D4 | The fix takes the usual path: work order, tier, checks, tower, two people, sealed release. | `test_fix_takes_the_usual_path` |
| D5 | The regression check is permanent: it is in the released skill the fix was built against. | `test_regression_check_is_in_the_skill` |
| D6 | A running case keeps its version after the fix is released; new cases get the fix. | `test_fix_reaches_new_cases_only` |
| D7 | A card drafted from production traces does not count until its owner signs it off. | `test_return_path_needs_the_owner` |

## Out of scope

Clustering of traces into candidate defects (manual here). The link to a work
tracking system. Scheduled improvement work (hillclimbing). Emergency change
beyond degraded mode. These are named in `docs/limits.md`.
