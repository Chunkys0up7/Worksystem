"""Calls one released version of the income decision service, in a fresh process.

Usage: python -m runtime.service_runner RELEASE_DIR  (inputs as JSON on stdin)

The service is imported from RELEASE_DIR, which is a stored, sealed release.
That is how a case keeps using the version it started with after a newer one
is released. A fresh process also means two versions never share an import.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from decimal import Decimal
from pathlib import Path


def main(argv: list[str]) -> int:
    release_dir = argv[0]
    inputs = json.loads(sys.stdin.read())
    # Load the service from the release directory BY PATH. Importing it by name
    # would be wrong: a stored release holds only the unit's own files, so
    # `services` there has no __init__.py, and Python prefers any regular
    # package of the same name elsewhere on the path. The case would silently
    # run whatever code is on main instead of the version it is pinned to.
    package = Path(release_dir) / "services" / "decision_income"
    spec = importlib.util.spec_from_file_location(
        "pinned_decision_income", package / "__init__.py",
        submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["pinned_decision_income"] = module
    spec.loader.exec_module(module)
    qualifying_monthly_income = module.qualifying_monthly_income
    loaded_from = Path(qualifying_monthly_income.__code__.co_filename).resolve()
    if Path(release_dir).resolve() not in loaded_from.parents:
        print(json.dumps({"error": f"service was loaded from {loaded_from}, outside the release"}))
        return 1

    args = [Decimal(inputs["base_per_period"]), inputs["frequency"],
            int(inputs["variable_history_months"]), Decimal(inputs["variable_total_in_window"])]
    kwargs = {}
    accepts = inspect.signature(qualifying_monthly_income).parameters
    if inputs.get("variable_total_recent") is not None and "variable_total_recent" in accepts:
        kwargs["variable_total_recent"] = Decimal(inputs["variable_total_recent"])
    try:
        result = qualifying_monthly_income(*args, **kwargs)
    except ValueError as exc:
        print(json.dumps({"refused": str(exc)}))
        return 0
    print(json.dumps({
        "qualifying_monthly": str(result.qualifying_monthly),
        "base_monthly": str(result.base_monthly),
        "variable_monthly": str(result.variable_monthly),
        "cards_applied": list(result.cards_applied),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
