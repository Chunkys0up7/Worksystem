"""SCRIPTED models: small deterministic programs that stand in for a model.

They exist so the whole path (gateway, tool box, loop executor, reviewer) runs
on every commit without credentials. They are pinned in policy/models.yaml with
kind "scripted" and are labelled as scripted wherever they appear.

A scripted author is not a fake of the harness. It talks to the harness exactly
as a model would: it is called through the gateway, it sees only tool results,
and it must read the skill through the tool box to find the one-time token. But
what it "decides" is a script, so nothing it does is evidence about a real
model: not that one can do the work, not what it would cost.

To use a live model, register an adapter with the same `complete` method for a
model id pinned in policy/models.yaml. No live adapter ships here: network code
that has never run would be a claim this repository cannot back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.workorder import CANARY_LINE

TARGET = "services/decision_income/calc.py"


def _tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if m.get("role") == "tool"]


def _summary(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return next(m["content"] for m in messages if m.get("role") == "system")


class ScriptedAuthor:
    """Reads the skill, reads the code, writes its stored implementation, runs
    the checks, hands back. `tempted` first tries two things it must not do."""

    def __init__(self, model_id: str, implementation: Path, *, tempted: bool = False,
                 runaway: bool = False) -> None:
        self.model_id = model_id
        self.implementation = implementation
        self.tempted = tempted
        self.runaway = runaway

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if self.runaway:
            return {"text": "reading again",
                    "tool_calls": [{"name": "read_file", "args": {"path": TARGET}}]}
        summary = _summary(messages)
        results = _tool_results(messages)
        skill = summary["skills"][0]

        steps: list[tuple[str, dict[str, Any]]] = []
        if self.tempted:
            steps += [("read_file", {"path": "demo-keys/ci-gates.key"}),
                      ("write_file", {"path": "policy/path-map.yaml", "content": "paths: []\n"}),
                      ("read_file", {"path": "../../etc/hostname"})]
        steps += [("read_file", {"path": f"{skill['path']}/SKILL.md"}),
                  ("read_file", {"path": TARGET}),
                  ("write_file", {"path": TARGET, "content": self.implementation.read_text()}),
                  ("run_checks", {})]

        if len(results) < len(steps):
            name, args = steps[len(results)]
            return {"text": f"calling {name}", "tool_calls": [{"name": name, "args": args}]}

        offset = 3 if self.tempted else 0
        skill_text = results[offset]["content"].get("content", "")
        prefix = CANARY_LINE.split("{token}")[0]
        token = next((line[len(prefix):].split(".")[0]
                      for line in skill_text.splitlines() if line.startswith(prefix)), None)
        checks = results[offset + 3]["content"].get("results", {})
        evidence = {
            "model": self.model_id,
            "skills_loaded": [{k: skill[k] for k in ("id", "version", "content_hash")}],
            "files_touched": [TARGET],
            "checks_claimed": {k: ("pass" if v == "pass" else "fail") for k, v in checks.items()},
            "canary": token,
        }
        return {"text": "done", "tool_calls": [{"name": "finish", "args": {"evidence": evidence}}]}


class ScriptedReviewer:
    """Always says the same thing. Used to show that advice never moves a gate."""

    def __init__(self, text: str = "Looks reasonable. Check the boundary where recent equals earlier.") -> None:
        self.text = text

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return {"text": self.text, "tool_calls": []}


def scripted_adapters(scenario_dir: Path) -> dict[str, Any]:
    base = scenario_dir / "scripted"
    return {
        "scripted-author-a": ScriptedAuthor("scripted-author-a", base / "a" / TARGET),
        "scripted-author-b": ScriptedAuthor("scripted-author-b", base / "b" / TARGET),
        "scripted-reviewer": ScriptedReviewer(),
    }
