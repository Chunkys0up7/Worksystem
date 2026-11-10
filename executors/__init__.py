"""Executor adapters. Each implements the contract in harness/workorder.py.

Nothing in harness/ imports from here: the harness depends on the contract,
never on an adapter. Adapters are looked up by name at the edge (the CLI and
the conformance suite).
"""

from __future__ import annotations

from pathlib import Path

from .recorded import RecordedExecutor

NAMES = ("recorded",)


def load(name: str, scenario_dir: Path):
    if name == "recorded":
        return RecordedExecutor(scenario_dir / "recordings" / "recorded")
    raise KeyError(
        f"no executor named {name!r}. Built so far: {', '.join(NAMES)}. "
        "The loop and cli executors are tasks 8 and 10 of SPEC-0001."
    )
