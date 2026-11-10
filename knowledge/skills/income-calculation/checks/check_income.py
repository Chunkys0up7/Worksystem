"""Paired deterministic checks for SKL-income-calculation.

Each function proves one card against the decision service. A check reads the
card's own data, so the card and the code cannot drift apart silently.
A check fails by raising AssertionError.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from services.decision_income import (
    PERIODS_PER_YEAR,
    VARIABLE_HISTORY_MIN_MONTHS,
    VARIABLE_WINDOW_MONTHS,
    qualifying_monthly_income,
)


def check_pay_periods_match_fact_card(ctx) -> None:
    """FACT-0001: the service's table equals the card's table."""
    card = ctx["cards"]["FACT-0001"]["data"]["periods_per_year"]
    assert PERIODS_PER_YEAR == card, (
        f"service table {PERIODS_PER_YEAR} differs from FACT-0001 {card}"
    )


def check_variable_income_history_rule(ctx) -> None:
    """RULE-0001: below the minimum history, variable income adds nothing."""
    data = ctx["cards"]["RULE-0001"]["data"]
    minimum, window = data["min_history_months"], data["window_months"]
    assert VARIABLE_HISTORY_MIN_MONTHS == minimum, "minimum history differs from RULE-0001"
    assert VARIABLE_WINDOW_MONTHS == window, "averaging window differs from RULE-0001"

    total = Decimal("24000.00")
    for months in range(0, minimum):
        result = qualifying_monthly_income(Decimal("5000"), "monthly", months, total)
        assert result.variable_monthly == 0, (
            f"{months} months of history must add nothing, added {result.variable_monthly}"
        )
    for months in (minimum, minimum + 1, 120):
        result = qualifying_monthly_income(Decimal("5000"), "monthly", months, total)
        assert result.variable_monthly == total / window, (
            f"{months} months must add total/{window}, added {result.variable_monthly}"
        )


def check_calculation_vectors(ctx) -> None:
    """CALC-0001: the service reproduces the SME-approved worked examples."""
    vectors = json.loads((Path(ctx["skill_dir"]) / "checks" / "vectors.json").read_text())
    for v in vectors:
        result = qualifying_monthly_income(
            Decimal(v["base_per_period"]),
            v["frequency"],
            v["history_months"],
            Decimal(v["variable_total"]),
        )
        assert result.qualifying_monthly == Decimal(v["expected"]), (
            f"{v}: got {result.qualifying_monthly}"
        )


def check_result_names_its_cards(ctx) -> None:
    """CALC-0001 with OBL-0001: every result names the cards that produced it."""
    plain = qualifying_monthly_income(Decimal("3000"), "monthly", 0, Decimal("0"))
    assert plain.cards_applied == ("CALC-0001", "FACT-0001"), plain.cards_applied
    variable = qualifying_monthly_income(Decimal("3000"), "monthly", 30, Decimal("2400"))
    assert variable.cards_applied == ("CALC-0001", "FACT-0001", "RULE-0001"), (
        variable.cards_applied
    )
    for card_id in variable.cards_applied:
        assert card_id in ctx["cards"], f"result names {card_id}, which is not a card"
