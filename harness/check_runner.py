"""Runs one skill's paired checks in a fresh process and prints JSON results.

Two roots, kept apart on purpose:

  ROOT       where the skill, its checks and the cards come from. Trusted:
             the released skill in the base repository.
  CODE_ROOT  where the governed code is imported from. Untrusted when it is an
             executor's worktree.

The harness and the skill are loaded before CODE_ROOT is put on the import
path, so nothing in a worktree can replace them. A separate process also means
nothing the checks import leaks into the caller. In a real pipeline this
process is where the sandbox goes: no network, no credentials, read-only
source. Here it is a plain subprocess. See docs/limits.md.

Usage: python -m harness.check_runner ROOT SKILL_ID [CODE_ROOT]
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve()
    skill_id = argv[1]
    code_root = Path(argv[2]).resolve() if len(argv) > 2 else root

    from harness.knowledge import load_knowledge

    knowledge = load_knowledge(root)
    skill = knowledge.skills[skill_id]
    ctx = {"root": str(code_root), "skill_dir": str(skill.dir), "cards": knowledge.cards}

    # Only now does the governed code become importable.
    sys.path[:] = [p for p in sys.path if p not in (str(root), str(code_root))]
    sys.path.insert(0, str(code_root))

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
