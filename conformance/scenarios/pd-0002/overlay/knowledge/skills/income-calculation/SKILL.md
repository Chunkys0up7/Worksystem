# Income calculation

Skill: SKL-income-calculation. Owner: income-policy-sme. Synthetic.

Load this skill before changing anything under `services/decision_income/`.

## What you must know

Qualifying monthly income is a regulated figure. It comes only from the
deterministic decision service. You may change that service to implement the
cards below. You may not move the calculation into a prompt, a model call or
any code path that is not exact arithmetic.

- FACT-0001: pay periods per year are 52 weekly, 26 biweekly, 24 semimonthly,
  12 monthly. The service's table must equal the card's table.
- RULE-0001: variable income counts only with at least 24 months of history,
  and is then averaged over 24 months. Below 24 months it contributes nothing.
- RULE-0002: when the recent 12-month total of variable income is supplied and
  is lower than the total of the 12 months before it, eligible variable income
  per month is the recent total divided by 12. Otherwise RULE-0001 applies
  unchanged. The history requirement in RULE-0001 always applies first. The
  service takes the recent total as an optional amount named
  `variable_total_recent`. It cannot be negative or exceed the window total.
- CALC-0001: base per period times periods per year divided by 12, plus
  eligible variable income per month. Round each part half up to the cent,
  then add.
- PROC-0001: when documents conflict, do not calculate. Route to a person.

## What you must do

1. Use `Decimal` for every amount. Never use binary floating point for money.
2. Keep the constants in the service equal to the `data` in the cards. If a
   rule changes, the card changes first, in its own reviewed change.
3. Return the IDs of the cards applied with every result.
4. Reject negative amounts and unknown pay frequencies. Do not guess.

## How this is checked

Each rule above has a paired deterministic check in `checks/`. CI runs them
under its own identity, whatever you report. The checks, not this text, decide
whether a change merges.
