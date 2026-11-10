"""Runs one skill's paired checks in a fresh process and prints JSON results.

A separate process means the checks import the governed code from the given
root and nothing else, and nothing they import leaks into the caller. In a
real pipeline this process is also where the sandbox goes: no network, no
credentials, read-only source. Here it is a plain subprocess. See docs/limits.md.

Usage: python -m harness.check_runner ROOT SKILL_ID
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve()
    skill_id = argv[1]
    sys.path[:] = [p for p in sys.path if p != str(root)]
    sys.path.insert(0, str(root))

    from harness.knowledge import load_knowledge

    knowledge = load_knowledge(root)
    skill = knowledge.skills[skill_id]
    ctx = {"root": str(root), "skill_dir": str(skill.dir), "cards": knowledge.cards}

    results = []
    for index, check in enumerate(skill.checks):
        entry = {"check_id": check["id"], "covers": list(check["covers"])}
        try:
            spec = importlib.util.spec_from_file_location(
                f"_skillcheck_{index}", skill.dir / check["module"]
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            getattr(module, check["function"])(ctx)
            entry.update(passed=True, detail="pass")
        except AssertionError as exc:
            entry.update(passed=False, detail=f"failed: {exc}")
        except Exception as exc:  # a check that cannot run has not passed
            entry.update(passed=False, detail=f"error: {type(exc).__name__}: {exc}")
        results.append(entry)

    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
