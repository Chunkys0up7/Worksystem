"""Held-out cases for the income decision service. SYNTHETIC.

Owned by the quality lead, not by the skill's SME and not by any executor. They
are never copied into a worktree, so an executor cannot see them, and a change
that fits the visible checks without implementing the rule fails here.

A case: arguments, then either `expected` (qualifying monthly income) or
`raises` (the call must be refused).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from services.decision_income import qualifying_monthly_income


def run(ctx):
    cases = json.loads((Path(ctx["dir"]) / "vectors.json").read_text())
    failures = []
    for index, case in enumerate(cases, start=1):
        args = [Decimal(case["base"]), case["frequency"], case["months"], Decimal(case["variable"])]
        kwargs = {}
        if "recent" in case:
            kwargs["variable_total_recent"] = Decimal(case["recent"])
        try:
            got = qualifying_monthly_income(*args, **kwargs).qualifying_monthly
        except ValueError:
            if not case.get("raises"):
                failures.append(f"case {index}: refused a valid input")
            continue
        except Exception as exc:
            failures.append(f"case {index}: {type(exc).__name__}")
            continue
        if case.get("raises"):
            failures.append(f"case {index}: accepted an input it must refuse")
        elif got != Decimal(case["expected"]):
            failures.append(f"case {index}: wrong result")
    return len(cases), failures
