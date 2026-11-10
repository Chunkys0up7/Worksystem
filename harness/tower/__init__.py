"""The confidence tower: the gates a change must pass, each with a number.

"No number, no merge." Each gate produces a figure and a threshold, written to
the ledger by the gates role. Which gates a change needs depends on its tier
(policy/tower.yaml), which is how review effort follows risk:

    fitness    architecture rules as code. Number: violations. All tiers.
    heldout    cases the executor never saw. Number: failures. Tier C.
    mutation   do the checks notice wrong code? Number: score. Tier C.

Every gate reads its rules, held-out cases and checks from the trusted
repository and applies them to the code under test. Nothing is read from an
executor's worktree except the code itself.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..checks import governing_skills, run_heldout, run_skill_checks
from ..delivery import Tower, TowerResult
from ..knowledge import Knowledge
from ..policy import Policy
from ..tiering.rulebook import glob_to_regex
from ..workorder import WorkOrder
from . import fitness, mutation

MAX_LISTED = 4


def _fitness(code_root: Path, policy: Policy) -> TowerResult:
    violations = fitness.run(code_root, policy.fitness_rules)
    return TowerResult("fitness", float(len(violations)), 0.0, not violations,
                       "; ".join(violations[:MAX_LISTED]))


def _heldout(code_root: Path, skills, knowledge: Knowledge) -> TowerResult:
    cases, failures, missing = 0, [], []
    for skill in skills:
        result = run_heldout(skill, knowledge, code_root)
        if result is None:
            missing.append(skill.id)
            continue
        cases += result[0]
        failures += result[1]
    if missing or not skills:
        # Tier C with no held-out cases has no number, and no number is not a pass.
        return TowerResult("heldout", None, 0.0, False,
                           f"no held-out cases for {', '.join(missing) or 'this change'}")
    return TowerResult("heldout", float(len(failures)), 0.0, not failures,
                       f"{cases} held-out cases" + (": " + "; ".join(failures[:MAX_LISTED])
                                                    if failures else ""))


def _killed(mutant_root: Path, skills, knowledge: Knowledge, policy: Policy) -> bool:
    for skill in skills:
        if any(not r.passed for r in run_skill_checks(skill, knowledge, policy,
                                                      code_root=mutant_root)):
            return True
        held = run_heldout(skill, knowledge, mutant_root)
        if held is not None and held[1]:
            return True
    return False


def _mutation(code_root: Path, changed: list[str], skills, knowledge: Knowledge,
              policy: Policy) -> TowerResult:
    threshold = float(policy.tower.get("mutation", {}).get("threshold", 0.8))
    governed = [glob_to_regex(g) for s in policy.skill_map
                if s["id"] in {k.id for k in skills} for g in s.get("governs", [])]
    targets = [p for p in changed if p.endswith(".py") and any(g.match(p) for g in governed)
               and (code_root / p).is_file()]
    if not targets:
        return TowerResult("mutation", None, threshold, False, "no critical module was changed")
    total, survivors = 0, []
    with tempfile.TemporaryDirectory(prefix="mutants-") as tmp:
        for rel in targets:
            original = (code_root / rel).read_text()
            top = rel.split("/")[0]
            for index, mutant in enumerate(mutation.mutants(original)):
                root = Path(tmp) / f"m{total}"
                shutil.copytree(code_root / top, root / top,
                                ignore=shutil.ignore_patterns("__pycache__"))
                (root / rel).write_text(mutant.source)
                total += 1
                if not _killed(root, skills, knowledge, policy):
                    survivors.append(f"{rel}:{mutant.line} {mutant.description}")
                shutil.rmtree(root)
    if not total:
        return TowerResult("mutation", None, threshold, False, "nothing to mutate")
    score = round((total - len(survivors)) / total, 3)
    detail = f"{total - len(survivors)} of {total} mutants killed"
    if survivors:
        detail += "; survivors: " + "; ".join(survivors[:MAX_LISTED])
        if len(survivors) > MAX_LISTED:
            detail += f" and {len(survivors) - MAX_LISTED} more"
    return TowerResult("mutation", score, threshold, score >= threshold, detail)


def make_tower(policy: Policy, knowledge: Knowledge) -> Tower:
    """`knowledge` is the trusted repository's. The code under test comes from the order."""

    def tower(order: WorkOrder, tier: str, changed: list[str]) -> list[TowerResult]:
        code_root = order.worktree
        skills = [knowledge.skills[s] for s in governing_skills(changed, policy)]
        results = []
        for gate in policy.tower.get("tiers", {}).get(tier, []):
            if gate == "fitness":
                results.append(_fitness(code_root, policy))
            elif gate == "heldout":
                results.append(_heldout(code_root, skills, knowledge))
            elif gate == "mutation":
                results.append(_mutation(code_root, changed, skills, knowledge, policy))
            else:
                results.append(TowerResult(gate, None, 0.0, False, "unknown gate in policy"))
            if not results[-1].passed:
                break  # a failed gate blocks; later, costlier gates are not run
        return results

    return tower


def tower_factory(env) -> Tower:
    return make_tower(env.policy, env.knowledge)
