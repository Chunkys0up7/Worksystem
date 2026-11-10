---
id: SPEC-0002
title: Falling variable income uses the recent 12 months
status: approved
cites: [PD-0002, OBL-0001]
allowed_paths:
  - "services/decision_income/**"
---

# SPEC-0002: Falling variable income uses the recent 12 months

Synthetic. The work order the stage 4 executors run.

## Intent

Implement PD-0002 in the income decision service. The rule is RULE-0002 and
its paired check is CHK-income-calculation-005, both delivered by skill
SKL-income-calculation 1.1.0. Read the skill before changing anything.

Outcome: CHK-income-calculation-005 passes, and checks 001 to 004 still pass.

## Acceptance

The five paired checks of SKL-income-calculation 1.1.0, run by CI.

## Constraints

Change only `services/decision_income/`. Do not change the skill, the cards or
any check. Existing callers that do not supply a recent figure must get the
same result as before.
