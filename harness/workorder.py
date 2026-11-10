"""Work orders and hand-backs: the two halves of the executor contract.

The harness builds the work order. The executor never does. It receives a
private copy of the repository (the worktree), the paths it may change, the
skills the skill map resolves for those paths, and nothing it should not have.

The hand-back is a claim. `validate_handback` trusts none of it: what changed
is derived by comparing the worktree to a snapshot taken before the executor
ran, not from what the executor says it touched.

No operating-system isolation is provided here. See docs/limits.md.
"""

from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import ids
from .canonical import sha256_hex
from .checks import skills_for_paths
from .knowledge import Knowledge, SkillInvalid, verify
from .policy import Policy
from .tiering.rulebook import glob_to_regex, normalise

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".hypothesis", ".ledger"}
CANARY_LINE = "Conformance token: {token}. Report it as `canary` in your evidence."


class WorkOrderRefused(Exception):
    """The work order cannot be built. Nothing is handed to an executor."""


@dataclass(frozen=True)
class SkillRef:
    id: str
    version: str
    content_hash: str
    path: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version, "content_hash": self.content_hash}

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass
class WorkOrder:
    change_id: str
    spec_id: str
    executor: str
    base_root: Path
    worktree: Path
    task: str
    allowed_paths: tuple[str, ...]
    deny_write: tuple[str, ...]
    skills: tuple[SkillRef, ...]
    manifest: dict[str, str]
    canary: str | None = None
    tools: Any = None            # harness.tools.ToolBox: pre-action deny enforcement
    gateway: Any = None          # harness.gateway.Gateway: the only route to a model
    reviewer_model: str | None = None
    launched_by: str | None = None  # the person who started this work; cannot approve it


@dataclass
class HandBack:
    """What an executor returns. Every field is a claim."""

    evidence: dict[str, Any]
    transcript: str = ""


class Executor(Protocol):
    """The whole contract an adapter implements."""

    id: str        # short product-neutral name, for example "recorded" or "loop"
    kind: str      # "recorded", "scripted" or "live"; anything but live is labelled
    version: str
    identity: str  # the workload identity it signs as; must hold the executor role

    def run(self, order: WorkOrder) -> HandBack: ...


@dataclass
class Verdict:
    accepted: bool
    changed: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


def _matches(globs: tuple[str, ...] | list[str], path: str) -> bool:
    return any(glob_to_regex(g).match(path) for g in globs)


def _walk(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if SKIP_DIRS.intersection(rel.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink() or path.is_file():
            yield rel.as_posix(), path


def snapshot(root: Path) -> dict[str, str]:
    """Path to content hash for every file. A symlink is recorded as a symlink."""
    out = {}
    for rel, path in _walk(root):
        out[rel] = "symlink" if path.is_symlink() else sha256_hex(path.read_bytes())
    return out


def build_work_order(
    change_id: str,
    spec_id: str,
    *,
    executor: str,
    root: Path,
    policy: Policy,
    knowledge: Knowledge,
    workdir: Path,
    with_canary: bool = False,
    gateway: Any = None,
    reviewer_model: str | None = None,
    launched_by: str | None = None,
) -> WorkOrder:
    if ids.kind_of(change_id) != "change":
        raise WorkOrderRefused(f"{change_id!r} is not a valid change id")
    spec = knowledge.specs.get(spec_id)
    if spec is None:
        raise WorkOrderRefused(f"{spec_id} is not a spec in this repository")
    if spec.status != "approved":
        raise WorkOrderRefused(f"{spec_id} is {spec.status}, not approved")
    ident = policy.identities.get(executor)
    if ident is None or ident.role != "executor":
        raise WorkOrderRefused(f"{executor} does not hold the executor role")

    allowed = tuple(spec.allowed_paths)
    worktree = workdir / "worktree"
    if worktree.exists():
        raise WorkOrderRefused(f"{worktree} already exists; a worktree is used once")
    for rel, path in _walk(root):
        if _matches(policy.deny_read, rel) or path.is_symlink():
            continue
        target = worktree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    in_scope = [rel for rel, _ in _walk(worktree) if _matches(allowed, rel)]
    skills = []
    for skill_id in skills_for_paths(in_scope, policy):
        skill = knowledge.skills.get(skill_id)
        if skill is None:
            raise WorkOrderRefused(f"skill map names {skill_id}, which does not exist")
        try:
            release = verify(skill, policy)
        except SkillInvalid as exc:
            raise WorkOrderRefused(str(exc)) from exc
        skills.append(
            SkillRef(skill.id, skill.version, release["content_hash"],
                     skill.dir.relative_to(root).as_posix())
        )

    canary = None
    if with_canary:
        # The token goes into the worktree copy of each skill's instructions.
        # The released skill in the base repository is untouched, and the
        # checks always run from that one.
        canary = secrets.token_hex(8)
        for ref in skills:
            target = worktree / ref.path / "SKILL.md"
            target.write_text(target.read_text() + "\n" + CANARY_LINE.format(token=canary) + "\n")

    from .checks import governing_skills, run_skill_checks
    from .tools import ToolBox

    def feedback() -> dict[str, str]:
        out: dict[str, str] = {}
        for skill_id in governing_skills(in_scope, policy):
            for result in run_skill_checks(knowledge.skills[skill_id], knowledge, policy,
                                           code_root=worktree):
                out[result.check_id] = "pass" if result.passed else f"fail: {result.detail}"
        return out

    tools = ToolBox(worktree, allowed, tuple(policy.deny_read), tuple(policy.deny_write),
                    check_runner=feedback)

    return WorkOrder(
        tools=tools,
        gateway=gateway,
        reviewer_model=reviewer_model,
        launched_by=launched_by,
        change_id=change_id,
        spec_id=spec_id,
        executor=executor,
        base_root=root,
        worktree=worktree,
        task=spec.path.read_text(),
        allowed_paths=allowed,
        deny_write=tuple(policy.deny_write),
        skills=tuple(skills),
        manifest=snapshot(worktree),
        canary=canary,
    )


def derive_changes(order: WorkOrder) -> list[str]:
    """Every path added, removed or altered since the work order was built."""
    now = snapshot(order.worktree)
    before = order.manifest
    return sorted(p for p in set(before) | set(now) if before.get(p) != now.get(p))


def validate_handback(order: WorkOrder, handback: HandBack) -> Verdict:
    evidence = handback.evidence if isinstance(handback.evidence, dict) else {}
    verdict = Verdict(accepted=False, changed=derive_changes(order))

    if not verdict.changed:
        verdict.violations.append("the hand-back changes nothing")
    now = snapshot(order.worktree)
    for path in verdict.changed:
        if now.get(path) == "symlink":
            verdict.violations.append(f"{path}: symbolic links are not accepted")
        norm = normalise(path)
        if norm is None or not _matches(order.allowed_paths, norm):
            verdict.violations.append(f"{path}: outside the paths the spec allows")
        elif _matches(order.deny_write, norm):
            verdict.violations.append(f"{path}: on the deny list")

    def key(row: Any) -> tuple:
        if not isinstance(row, dict):
            return ("?",)
        return (row.get("id"), row.get("version"), row.get("content_hash"))

    claimed = sorted(key(r) for r in evidence.get("skills_loaded") or [])
    given = sorted(key(s.as_dict()) for s in order.skills)
    if claimed != given:
        def show(row: tuple) -> str:
            return f"{row[0]}@{row[1]}#{str(row[2])[:12]}" if len(row) == 3 else "unreadable"

        verdict.violations.append(
            "skills loaded do not match the work order: "
            f"given {[show(g) for g in given]}, claimed {[show(c) for c in claimed]}"
        )

    executor = evidence.get("executor")
    if not isinstance(executor, dict) or not executor.get("id"):
        verdict.violations.append("evidence does not say which executor produced it")

    if order.canary is not None and evidence.get("canary") != order.canary:
        verdict.violations.append(
            "the one-time skill token was not returned: no proof the skill text "
            "reached the executor"
        )

    touched = evidence.get("files_touched")
    if sorted(touched or []) != verdict.changed:
        verdict.mismatches.append(
            f"files: executor claimed {sorted(touched or [])}, harness derived {verdict.changed}"
        )

    verdict.accepted = not verdict.violations
    return verdict
