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
    extends: str | None = None
    defect: str | None = None


def load_scenario(root: Path, name: str) -> Scenario:
    directory = root / "conformance" / "scenarios" / name
    if not (directory / "scenario.yaml").exists():
        raise KeyError(f"no scenario named {name!r}")
    data = yaml.safe_load((directory / "scenario.yaml").read_text())
    return Scenario(directory, data["id"], data["title"], data["spec"], data["change"],
                    data["decision"], data["red_check"], data.get("extends"), data.get("defect"))


def overlays(scenario: Scenario) -> list[Path]:
    """This scenario's overlay, preceded by those of every scenario it extends."""
    chain = [scenario]
    while chain[0].extends:
        chain.insert(0, load_scenario(scenario.dir.parents[2], chain[0].extends))
    return [s.dir / "overlay" for s in chain]


def prepare(scenario: Scenario, base_root: Path, dest: Path) -> Path:
    """Copy the repository to `dest`, then lay the scenario's overlay over it."""
    base, target = base_root.resolve(), dest.resolve()
    # When the copy is made somewhere inside the repository being copied, the
    # directory that holds it must not be copied into itself.
    inside = target.relative_to(base).parts[0] if base in target.parents else None

    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n in SKIP_DIRS or n.endswith(".pyc")}
        if inside and Path(directory).resolve() == base:
            skip.add(inside)
        return skip

    shutil.copytree(base_root, dest, ignore=ignore)
    for overlay in overlays(scenario):
        for source in sorted(overlay.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts:
                target = dest / source.relative_to(overlay)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    return dest
