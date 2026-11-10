---
id: SPEC-0010
title: Refuse amounts that are not finite numbers
status: approved
cites: [OBL-0001]
fixes: [DEF-0001]
allowed_paths:
  - "services/decision_income/**"
---

# SPEC-0010: Refuse amounts that are not finite numbers

Synthetic. The fix for DEF-0001.

## The defect

A case in production halted because the income decision service failed on an
amount that was not a number. Reproducing it showed two faults: the service
fails with an arithmetic error on not-a-number and on infinity, and in one
path it accepts an infinite amount and returns a result as if nothing were
wrong.

The defect was reproduced against the released version before this spec was
approved. The reproduction is on the ledger.

## Intent

The service refuses any amount that is not a finite number, with ValueError,
before doing any arithmetic. Nothing else about its behaviour changes.

## Acceptance

CHK-income-calculation-006, added to the skill as the permanent regression
check, and the held-out cases added for this defect. Checks 001 to 005 still
pass.

## Constraints

Change only `services/decision_income/`. Do not change the skill or any check.
