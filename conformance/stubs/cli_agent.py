"""A STUB coding agent for the CLI executor. It is a script, not an agent.

It stands in for a headless coding-agent product so the CLI executor's path can
run without one. It behaves the way such a product would at the boundary: it is
started in the worktree, reads a task file, reads the skill from disk with its
own file access, writes the change, and writes its evidence where it was told.

What it writes is a stored implementation (STUB_IMPLEMENTATION). Nothing it
does says anything about a real product.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

TARGET = "services/decision_income/calc.py"
PREFIX = "Conformance token: "


def main() -> None:
    task = json.loads(Path(os.environ["HARNESS_TASK"]).read_text())
    token = None
    for skill in task["skills"]:
        for line in Path(skill["path"], "SKILL.md").read_text().splitlines():
            if line.startswith(PREFIX):
                token = line[len(PREFIX):].split(".")[0]
    shutil.copyfile(os.environ["STUB_IMPLEMENTATION"], TARGET)
    Path(task["handback"]).write_text(json.dumps({
        "model": "stub (no model)",
        "skills_loaded": [{k: s[k] for k in ("id", "version", "content_hash")} for s in task["skills"]],
        "files_touched": [TARGET],
        "checks_claimed": {},
        "canary": token,
    }))


if __name__ == "__main__":
    main()
