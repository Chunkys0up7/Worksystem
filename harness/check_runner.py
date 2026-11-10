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


def strays(code_root: Path) -> list[str]:
    """Modules that belong to the code under test and were loaded from elsewhere.

    The checks import the governed code by name. If the path is wrong, Python
    can quietly import a package of the same name from another place (the
    harness's own repository has one). A check that ran against the wrong code
    proves nothing, so this is a failure, not a warning.
    """
    tops = {p.name for p in code_root.iterdir() if p.is_dir()} - {"harness"}
    out = []
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if not origin or name.split(".")[0] not in tops:
            continue
        if code_root not in Path(origin).resolve().parents:
            out.append(f"{name} was imported from {origin}, not from the code under test")
    return sorted(out)


def heldout(argv: list[str]) -> int:
    """--heldout ROOT NAME CODE_ROOT: run ROOT/heldout/NAME/run.py against CODE_ROOT."""
    root, name, code_root = Path(argv[0]).resolve(), argv[1], Path(argv[2]).resolve()
    directory = root / "heldout" / name
    spec = importlib.util.spec_from_file_location("_heldout", directory / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.path[:] = [p for p in sys.path if p not in (str(root), str(code_root))]
    sys.path.insert(0, str(code_root))
    spec.loader.exec_module(module)
    cases, failures = module.run({"root": str(code_root), "dir": str(directory)})
    print(json.dumps({"cases": cases, "failures": list(failures) + strays(code_root)}))
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--heldout":
        return heldout(argv[1:])
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

    wrong = strays(code_root)
    if wrong:
        for entry in results:
            entry.update(passed=False, detail=f"error: {wrong[0]}")
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
