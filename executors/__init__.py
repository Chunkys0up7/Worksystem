"""Executor adapters. Each implements the contract in harness/workorder.py.

Nothing in harness/ imports from here: the harness depends on the contract,
never on an adapter. Adapters are looked up by name at the edge (the CLI and
the conformance suite).

    recorded   replays a stored hand-back. No model.
    loop       a small tool loop over the gateway, with scripted model A.
    loop-b     the same loop with scripted model B, a different "family".
    cli-stub   the CLI executor driving a stub script in place of a product.
    cli        the CLI executor driving a real headless coding-agent command,
               given in HARNESS_CLI_COMMAND. Live. Not exercised in this build.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .cli import CliExecutor
from .loop import LoopExecutor
from .recorded import RecordedExecutor

NAMES = ("recorded", "loop", "loop-b", "cli-stub", "cli")
LOOP_MODELS = {"loop": "scripted-author-a", "loop-b": "scripted-author-b"}


def load(name: str, scenario_dir: Path, policy=None):
    if name == "recorded":
        return RecordedExecutor(scenario_dir / "recordings" / "recorded")
    if name in LOOP_MODELS:
        model_id = LOOP_MODELS[name]
        kind = "scripted"
        if policy is not None and model_id in (policy.models or {}):
            kind = policy.models[model_id].get("kind", "live")
        return LoopExecutor(model_id, kind)
    if name == "cli-stub":
        stub = Path(__file__).resolve().parent.parent / "conformance" / "stubs" / "cli_agent.py"
        return CliExecutor(
            [sys.executable, str(stub)], "scripted",
            extra_env={"STUB_IMPLEMENTATION": str(
                scenario_dir / "scripted" / "b" / "services" / "decision_income" / "calc.py")})
    if name == "cli":
        command = os.environ.get("HARNESS_CLI_COMMAND")
        if not command:
            raise KeyError("the cli executor needs HARNESS_CLI_COMMAND: the headless "
                           "coding-agent command to run inside the worktree")
        return CliExecutor(shlex.split(command), "live")
    raise KeyError(f"no executor named {name!r}. Available: {', '.join(NAMES)}.")
