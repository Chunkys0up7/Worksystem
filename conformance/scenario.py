"""Scenarios: a repeatable starting state for an executor to work from.

A scenario lays an overlay (changes made by people) over a copy of the
repository. The result is a complete repository root in which a spec is
approved, a skill has been re-released, and a paired check is red.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from harness.workorder import SKIP_DIRS


@dataclass(frozen=True)
class Scenario:
    dir: Path
    id: str
    title: str
    spec: str
    change: str
    decision: str
    red_check: str


def load_scenario(root: Path, name: str) -> Scenario:
    directory = root / "conformance" / "scenarios" / name
    if not (directory / "scenario.yaml").exists():
        raise KeyError(f"no scenario named {name!r}")
    data = yaml.safe_load((directory / "scenario.yaml").read_text())
    return Scenario(directory, data["id"], data["title"], data["spec"], data["change"],
                    data["decision"], data["red_check"])


def prepare(scenario: Scenario, base_root: Path, dest: Path) -> Path:
    """Copy the repository to `dest`, then lay the scenario's overlay over it."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        return {n for n in names if n in SKIP_DIRS or n.endswith(".pyc")}

    shutil.copytree(base_root, dest, ignore=ignore)
    overlay = scenario.dir / "overlay"
    for source in sorted(overlay.rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts:
            target = dest / source.relative_to(overlay)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return dest
