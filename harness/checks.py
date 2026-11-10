"""Runs a skill's paired checks under the CI identity and records the results.

The executor's own report is never the evidence. CI verifies the skill is the
released one, runs its checks against the code as it stands, and binds each
result to the skill's content hash and the hash of the code it governs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from .canonical import hash_obj, sha256_hex
from .gates import GateOutcome, record_or_block
from .knowledge import Knowledge, Skill, verify
from .ledger import Ledger, sign_submission
from .policy import Policy
from .tiering.rulebook import glob_to_regex

CI_GATES = "ci-gates"
RUNNER_TIMEOUT_SECONDS = 120


@dataclass
class CheckResult:
    check_id: str
    covers: list[str]
    passed: bool
    detail: str


def governed_code_hash(root: Path, policy: Policy, skill_id: str) -> str:
    """Hash of every file the skill governs, as it is on disk right now."""
    entry = next((s for s in policy.skill_map if s["id"] == skill_id), None)
    patterns = [glob_to_regex(g) for g in (entry or {}).get("governs", [])]
    listing = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if any(p.match(rel) for p in patterns):
            listing.append([rel, sha256_hex(path.read_bytes())])
    return hash_obj(listing)


def skills_for_paths(paths: list[str], policy: Policy) -> list[str]:
    """Skills that govern any of these paths, or that are themselves changed."""
    out = []
    for entry in policy.skill_map:
        globs = list(entry.get("governs", [])) + [entry["path"]]
        if any(glob_to_regex(g).match(p) for g in globs for p in paths):
            out.append(entry["id"])
    return out


def governing_skills(paths: list[str], policy: Policy) -> list[str]:
    """Skills whose governed code these paths touch (not the skill's own files)."""
    out = []
    for entry in policy.skill_map:
        if any(glob_to_regex(g).match(p) for g in entry.get("governs", []) for p in paths):
            out.append(entry["id"])
    return out


def run_skill_checks(
    skill: Skill, knowledge: Knowledge, policy: Policy, code_root: Path | None = None
) -> list[CheckResult]:
    """Verify the skill, then run its paired checks in a fresh process.

    The skill and cards come from `knowledge.root`, which is trusted. The
    governed code comes from `code_root`, which may be an executor's worktree.

    Raises SkillInvalid if the skill is unreleased or altered. If the runner
    fails for any reason, every check is reported as failed: a check that
    could not run has not passed.
    """
    verify(skill, policy)
    root = knowledge.root
    code_root = code_root or root
    harness_home = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = harness_home
    try:
        # Checks run against source, never against cached bytecode. A stale
        # .pyc beside an edited file (same size, same second) would otherwise
        # let a check pass against code that is no longer there. The runner
        # gets an empty, private bytecode cache.
        with tempfile.TemporaryDirectory(prefix="check-pycache-") as cache:
            env["PYTHONPYCACHEPREFIX"] = cache
            proc = subprocess.run(
                [sys.executable, "-m", "harness.check_runner",
                 str(root), skill.id, str(code_root)],
                capture_output=True, text=True, timeout=RUNNER_TIMEOUT_SECONDS,
                cwd=harness_home, env=env,
            )
        rows = json.loads(proc.stdout) if proc.returncode == 0 else None
        problem = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "runner failed"
    except subprocess.TimeoutExpired:
        rows, problem = None, f"runner timed out after {RUNNER_TIMEOUT_SECONDS}s"
    except json.JSONDecodeError:
        rows, problem = None, "runner output was not readable"

    if rows is None or [r["check_id"] for r in rows] != [c["id"] for c in skill.checks]:
        return [
            CheckResult(c["id"], list(c["covers"]), False, f"error: {problem}")
            for c in skill.checks
        ]
    return [CheckResult(r["check_id"], r["covers"], bool(r["passed"]), r["detail"]) for r in rows]


def record_results(
    ledger: Ledger,
    keydir: Path,
    skill: Skill,
    results: list[CheckResult],
    policy: Policy,
    root: Path,
    release: dict[str, str],
    change_id: str | None = None,
    extra_refs: tuple[str, ...] = (),
) -> list[GateOutcome]:
    code_hash = governed_code_hash(root, policy, skill.id)
    outcomes = []
    for result in results:
        payload = {
            "subject": result.check_id,
            "skill": skill.ref,
            "skill_content_hash": release["content_hash"],
            "covers": result.covers,
            "outcome": "pass" if result.passed else "fail",
            "detail": result.detail,
            "governed_code_hash": code_hash,
        }
        refs = [result.check_id, skill.id, skill.ref, *result.covers, *skill.implements]
        if change_id:
            payload["change"] = change_id
            refs.append(change_id)
        refs.extend(extra_refs)
        submission = sign_submission(keydir, CI_GATES, "check.result", payload, refs)
        outcomes.append(record_or_block(ledger, submission, result.passed))
    return outcomes
