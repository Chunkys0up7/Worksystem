"""Deterministic decision service: qualifying monthly income.

SYNTHETIC. The rules here are invented for this reference build. They are not
any agency's, investor's or institution's underwriting guideline.

A regulated decision comes only from a service like this one: plain code, exact
arithmetic, no model in the path. An agent may call it; an agent may not
replace it. Every result names the cards that produced it, so the decision can
be explained from recorded inputs (OBL-0001).

Governed by skill SKL-income-calculation. Cards: FACT-0001, RULE-0001, RULE-0002,
CALC-0001.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# FACT-0001: pay periods per year, by pay frequency.
PERIODS_PER_YEAR: dict[str, int] = {
    "weekly": 52,
    "biweekly": 26,
    "semimonthly": 24,
    "monthly": 12,
}

# RULE-0001: variable income counts only with at least this much history,
# and is averaged over this window.
VARIABLE_HISTORY_MIN_MONTHS = 24
VARIABLE_WINDOW_MONTHS = 24

# RULE-0002: when variable income has fallen, the recent window is used.
VARIABLE_RECENT_WINDOW_MONTHS = 12

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class IncomeResult:
    qualifying_monthly: Decimal
    base_monthly: Decimal
    variable_monthly: Decimal
    cards_applied: tuple[str, ...]


def qualifying_monthly_income(
    base_per_period: Decimal,
    frequency: str,
    variable_history_months: int,
    variable_total_in_window: Decimal,
    variable_total_recent: Decimal | None = None,
) -> IncomeResult:
    """CALC-0001: base income per month plus eligible variable income per month.

    `variable_total_recent` is the variable income of the most recent 12 months.
    It is optional; without it RULE-0002 cannot apply and RULE-0001 stands.
    """
    if frequency not in PERIODS_PER_YEAR:
        raise ValueError(f"unknown pay frequency: {frequency!r}")
    base_per_period = Decimal(base_per_period)
    variable_total_in_window = Decimal(variable_total_in_window)
    for amount in (base_per_period, variable_total_in_window):
        if not amount.is_finite():
            raise ValueError("income amounts must be finite numbers")
    if base_per_period < 0 or variable_total_in_window < 0:
        raise ValueError("income amounts cannot be negative")
    if variable_history_months < 0:
        raise ValueError("history months cannot be negative")
    if variable_total_recent is not None:
        variable_total_recent = Decimal(variable_total_recent)
        if not variable_total_recent.is_finite():
            raise ValueError("income amounts must be finite numbers")
        if variable_total_recent < 0 or variable_total_recent > variable_total_in_window:
            raise ValueError("recent variable income must be between zero and the window total")

    cards = ["FACT-0001", "CALC-0001"]
    base_monthly = base_per_period * PERIODS_PER_YEAR[frequency] / Decimal(12)

    variable_monthly = Decimal(0)
    if variable_total_in_window > 0:
        cards.append("RULE-0001")
        if variable_history_months >= VARIABLE_HISTORY_MIN_MONTHS:
            variable_monthly = variable_total_in_window / Decimal(VARIABLE_WINDOW_MONTHS)
            if variable_total_recent is not None:
                earlier = variable_total_in_window - variable_total_recent
                if variable_total_recent < earlier:
                    cards.append("RULE-0002")
                    variable_monthly = variable_total_recent / Decimal(
                        VARIABLE_RECENT_WINDOW_MONTHS
                    )

    base_monthly = base_monthly.quantize(_CENT, rounding=ROUND_HALF_UP)
    variable_monthly = variable_monthly.quantize(_CENT, rounding=ROUND_HALF_UP)
    return IncomeResult(
        qualifying_monthly=base_monthly + variable_monthly,
        base_monthly=base_monthly,
        variable_monthly=variable_monthly,
        cards_applied=tuple(sorted(cards)),
    )
