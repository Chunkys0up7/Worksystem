"""Commit trailers: the link from a change in git back to its spec and skills.

    Spec: SPEC-0002
    Change: CHG-pd-0002
    Executor: recorded@1 (recorded)
    Skills-Loaded: SKL-income-calculation@1.1.0#a1b2c3d4e5f6

Every commit cites a spec and stays inside the paths that spec allows. A commit
that touches skill-governed code also names its change, its executor and the
released skill versions that were loaded. A missing or false trailer fails the
build.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import ids
from .checks import governing_skills
from .knowledge import Knowledge
from .policy import Policy
from .tiering.retro import parse_name_status
from .tiering.rulebook import glob_to_regex
from .workorder import WorkOrder

_LINE = re.compile(r"^(Spec|Change|Executor|Skills-Loaded):\s*(.+?)\s*$", re.M)
_SKILL = re.compile(r"^(SKL-[a-z0-9-]+)@(\d+\.\d+\.\d+)#([0-9a-f]{12,64})$")
HASH_PREFIX = 12


def render(order: WorkOrder, executor) -> str:
    label = "" if executor.kind == "live" else f" ({executor.kind})"
    lines = [
        f"Spec: {order.spec_id}",
        f"Change: {order.change_id}",
        f"Executor: {executor.id}@{executor.version}{label}",
    ]
    for skill in order.skills:
        lines.append(f"Skills-Loaded: {skill.ref}#{skill.content_hash[:HASH_PREFIX]}")
    return "\n".join(lines)


def parse(message: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, value in _LINE.findall(message):
        out.setdefault(key, []).append(value)
    return out


def check(
    message: str,
    changed_paths: list[str],
    knowledge: Knowledge,
    policy: Policy,
    released: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Problems with one commit. `released` maps skill id to its release record
    as of that commit; by default the releases in `knowledge` are used."""
    trailers = parse(message)
    problems: list[str] = []

    specs = trailers.get("Spec", [])
    if len(specs) != 1:
        return [f"needs exactly one Spec trailer, found {len(specs)}"]
    spec = knowledge.specs.get(specs[0])
    if spec is None:
        return [f"cites {specs[0]}, which is not a spec in this repository"]
    if spec.status not in ("approved", "done"):
        problems.append(f"{spec.id} is {spec.status}, not approved")
    patterns = [glob_to_regex(g) for g in spec.allowed_paths]
    for path in changed_paths:
        if not any(p.match(path) for p in patterns):
            problems.append(f"{path}: outside the paths {spec.id} allows")

    governed = governing_skills(changed_paths, policy)
    if not governed:
        return problems

    changes = trailers.get("Change", [])
    if len(changes) != 1 or ids.kind_of(changes[0]) != "change":
        problems.append("touches skill-governed code and needs one valid Change trailer")
    if len(trailers.get("Executor", [])) != 1:
        problems.append("touches skill-governed code and needs one Executor trailer")

    loaded: dict[str, tuple[str, str]] = {}
    for value in trailers.get("Skills-Loaded", []):
        match = _SKILL.match(value)
        if not match:
            problems.append(f"Skills-Loaded value {value!r} is not SKL-id@version#hash")
            continue
        loaded[match.group(1)] = (match.group(2), match.group(3))

    if released is None:
        released = {}
        for skill_id in governed:
            skill = knowledge.skills.get(skill_id)
            path = skill.dir / "release.json" if skill else None
            if path and path.exists():
                released[skill_id] = json.loads(path.read_text())
    for skill_id in governed:
        if skill_id not in loaded:
            problems.append(f"{skill_id} governs this code and is not in Skills-Loaded")
            continue
        version, digest = loaded[skill_id]
        release = released.get(skill_id)
        if release is None:
            problems.append(f"{skill_id} has no release to compare against")
        elif release.get("version") != version or not str(
            release.get("content_hash", "")
        ).startswith(digest):
            problems.append(
                f"{skill_id}@{version}#{digest[:HASH_PREFIX]} is not the released skill "
                f"({release.get('version')}#{str(release.get('content_hash'))[:HASH_PREFIX]})"
            )
    return problems


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def check_commits(repo: Path, knowledge: Knowledge, policy: Policy) -> list[tuple[str, list[str]]]:
    """Check every commit after the baseline. Fails closed if the baseline is unknown."""
    baseline = policy.enforce_commits_after
    if not baseline:
        return [("policy", ["policy/commits.yaml sets no baseline"])]
    try:
        _git(repo, "merge-base", "--is-ancestor", baseline, "HEAD")
        shas = _git(repo, "rev-list", "--no-merges", "--reverse", f"{baseline}..HEAD").split()
    except subprocess.CalledProcessError:
        return [(baseline[:10], ["baseline commit is not in this history; cannot check commits"])]

    results = []
    for sha in shas:
        message = _git(repo, "log", "-1", "--format=%B", sha)
        paths = parse_name_status(
            _git(repo, "diff-tree", "--no-commit-id", "--name-status", "-M", "-r", "--root", sha)
        )
        released = {}
        for skill_id in governing_skills(paths, policy):
            skill = knowledge.skills.get(skill_id)
            if skill is None:
                continue
            rel = (skill.dir / "release.json").relative_to(knowledge.root).as_posix()
            try:
                released[skill_id] = json.loads(_git(repo, "show", f"{sha}:{rel}"))
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                pass
        results.append((sha[:10], check(message, paths, knowledge, policy, released)))
    return results
