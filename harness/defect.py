"""The defect loop: from a trace in production back to a permanent test.

    trace -> defect opened -> REPRODUCED against the released version
          -> a permanent regression check and held-out cases (people)
          -> a spec that names the defect -> a work order -> the usual gates
          -> a release

Two rules make it a loop and not a shortcut:

- Reproduce before fixing. A spec that fixes a defect gets no work order until
  the defect has been reproduced, by the gates role, against the version that
  was released. A fix for something nobody reproduced is a guess.
- No out-of-band hotfix. The fix is a change like any other: same tier rules,
  same tower, same two people. The only fast path is degraded mode, which
  waives nothing.

A defect cites the ledger entries that show it (a halted case, a tripped
guard). It carries no personal data: those entries hold references.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import ids
from .checks import run_heldout, run_skill_checks
from .knowledge import Knowledge, Skill
from .ledger import Entry, Ledger, sign_submission
from .policy import Policy

TRIAGE_IDENTITY = "ci-triage"
GATES_IDENTITY = "ci-gates"


class DefectError(Exception):
    pass


def open_defect(defect_id: str, title: str, *, traces: list[str], unit: str, version: str,
                ledger: Ledger, keydir: Path) -> Entry:
    if ids.kind_of(defect_id) != "defect":
        raise DefectError(f"{defect_id!r} is not a valid defect id")
    known = {e.id for e in ledger.entries()}
    missing = [t for t in traces if t not in known]
    if not traces or missing:
        raise DefectError(
            "a defect cites the ledger entries that show it"
            + (f"; not in the ledger: {', '.join(missing)}" if missing else ""))
    return ledger.append(sign_submission(
        keydir, TRIAGE_IDENTITY, "defect.opened",
        {"subject": defect_id, "title": title, "unit": unit, "version": version,
         "traces": traces}, [defect_id, *traces]))


def materialise(release_dir: Path, dest: Path) -> Path:
    """A stored release as an importable code root (a release holds only the unit's files)."""
    shutil.copytree(release_dir, dest, ignore=shutil.ignore_patterns("manifest.json", "__pycache__"))
    for directory in sorted(p for p in dest.rglob("*") if p.is_dir()):
        if any(directory.glob("*.py")) or any(directory.iterdir()):
            parent = directory
            while parent != dest:
                init = parent / "__init__.py"
                if not init.exists():
                    init.write_text("")
                parent = parent.parent
    return dest


def reproduce(defect_id: str, skill: Skill, *, knowledge: Knowledge, policy: Policy,
              code_root: Path, against: str, ledger: Ledger, keydir: Path) -> Entry:
    """Run the regression cases against the code as released. They must FAIL.

    `knowledge` is the trusted repository, which already holds the new
    regression check and held-out cases. `code_root` is the released code.
    """
    if not any(e.payload.get("subject") == defect_id
               for e in ledger.find(fact_type="defect.opened")):
        raise DefectError(f"{defect_id} has not been opened")
    failing = [r.check_id for r in run_skill_checks(skill, knowledge, policy, code_root=code_root)
               if not r.passed]
    held = run_heldout(skill, knowledge, code_root)
    held_failures = len(held[1]) if held else 0
    reproduced = bool(failing) or held_failures > 0
    return ledger.append(sign_submission(
        keydir, GATES_IDENTITY, "reproduction.result",
        {"subject": defect_id, "reproduced": reproduced, "against": against,
         "failing_checks": failing, "heldout_failures": held_failures,
         "skill": skill.ref}, [defect_id, skill.id, *failing]))


def reproduce_against_release(defect_id: str, skill: Skill, *, knowledge: Knowledge,
                              policy: Policy, releases_dir: Path, unit: str, version: str,
                              ledger: Ledger, keydir: Path) -> Entry:
    with tempfile.TemporaryDirectory(prefix="reproduce-") as tmp:
        root = materialise(releases_dir / unit / version, Path(tmp) / "release")
        return reproduce(defect_id, skill, knowledge=knowledge, policy=policy, code_root=root,
                         against=f"{unit}@{version}", ledger=ledger, keydir=keydir)


def not_reproduced(defect_ids: list[str], ledger: Ledger) -> list[str]:
    """Defects a spec says it fixes that have no successful reproduction on the ledger."""
    done = {e.payload["subject"] for e in ledger.find(fact_type="reproduction.result")
            if e.payload.get("reproduced") is True}
    return [d for d in defect_ids if d not in done]
