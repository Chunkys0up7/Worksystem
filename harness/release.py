"""Approval and release. Deploy is not release, and a gate is not an approval.

A release is decided by rules, from what is already in the ledger:

- there is a gate result for the change, and it is not blocked
- Tier A and B: nothing more is asked
- Tier C: two approvals, from two different people, neither of whom launched
  the work, each bound to this exact gate result. An approval given for an
  earlier gate result does not carry over to a re-run.
- the ledger is intact

The executor cannot approve (its role cannot write an approval). The launcher
cannot approve (the rule below). The release identity cannot approve either: it
only counts.

A release seals a manifest: what was released, from which change and spec, at
which tier, with which skills and models, on which evidence, approved by whom.
It also stores the released files by version, so a running case can keep using
the version it started with.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import hash_obj, sha256_hex
from .ledger import Entry, Ledger, sign_submission
from .policy import Policy
from .tiering.rulebook import glob_to_regex

RELEASE_IDENTITY = "ci-release"


class ReleaseRefused(Exception):
    pass


@dataclass
class Release:
    unit: str
    version: str
    manifest: dict[str, Any]
    entry: Entry
    path: Path | None


def _latest_gate(ledger: Ledger, change_id: str) -> Entry | None:
    gates = [e for e in ledger.find(fact_type="gate.result", ref=change_id)
             if e.payload.get("subject") == change_id]
    return gates[-1] if gates else None


def approve(ledger: Ledger, keydir: Path, approver: str, change_id: str,
            decision: str = "approve", note: str = "") -> Entry:
    """A person records a decision on the current gate result of a change."""
    gate = _latest_gate(ledger, change_id)
    if gate is None:
        raise ReleaseRefused(f"{change_id} has no gate result to approve")
    if decision not in ("approve", "reject"):
        raise ReleaseRefused("a decision is approve or reject")
    return ledger.append(sign_submission(
        keydir, approver, "approval.recorded",
        {"subject": change_id, "decision": decision, "gate_ref": gate.id,
         "gate_hash": gate.entry_hash, "note": note}, [change_id, gate.id]))


def unit_of(policy: Policy, paths: list[str]) -> dict[str, Any] | None:
    for unit in policy.release_units:
        patterns = [glob_to_regex(g) for g in unit["paths"]]
        if any(p.match(path) for p in patterns for path in paths):
            return unit
    return None


def _refuse(ledger: Ledger, keydir: Path, change_id: str, reason: str) -> ReleaseRefused:
    try:
        ledger.append(sign_submission(keydir, RELEASE_IDENTITY, "release.refused",
                                      {"subject": change_id, "reason": reason}, [change_id]))
    except Exception:  # the refusal stands whether or not it could be recorded
        pass
    return ReleaseRefused(reason)


def release(change_id: str, *, ledger: Ledger, keydir: Path, policy: Policy,
            code_root: Path, releases_dir: Path) -> Release:
    problems = ledger.verify()
    if problems:
        raise _refuse(ledger, keydir, change_id, f"the ledger is not intact: {problems[0]}")
    gate = _latest_gate(ledger, change_id)
    if gate is None:
        raise _refuse(ledger, keydir, change_id, "no gate result for this change")
    if any(e.payload.get("gate_ref") == gate.id
           for e in ledger.find(fact_type="release.approved", ref=change_id)):
        raise _refuse(ledger, keydir, change_id, f"{gate.id} has already been released")
    outcome, tier = gate.payload["outcome"], gate.payload.get("tier")
    if outcome == "blocked":
        raise _refuse(ledger, keydir, change_id, f"the gate is blocked: {gate.payload['why']}")

    launcher = gate.payload.get("launched_by")
    approvals: list[Entry] = []
    if outcome == "held-for-approval":
        need = int(policy.tower.get("release", {}).get("approvals_required", 2))
        bound = [e for e in ledger.find(fact_type="approval.recorded", ref=change_id)
                 if e.payload.get("gate_ref") == gate.id
                 and e.payload.get("gate_hash") == gate.entry_hash]
        rejected = [e for e in bound if e.payload["decision"] == "reject"]
        if rejected:
            raise _refuse(ledger, keydir, change_id,
                          f"rejected by {rejected[0].actor} in {rejected[0].id}")
        seen: set[str] = set()
        for entry in bound:
            if entry.actor == launcher or entry.actor in seen:
                continue  # the launcher cannot approve; one person counts once
            seen.add(entry.actor)
            approvals.append(entry)
        if len(approvals) < need:
            detail = f"{len(approvals)} of {need} approvals bound to {gate.id}"
            if any(e.actor == launcher for e in bound):
                detail += f"; {launcher} launched this work and cannot approve it"
            raise _refuse(ledger, keydir, change_id, detail)
    elif outcome != "passed":
        raise _refuse(ledger, keydir, change_id, f"unknown gate outcome {outcome!r}")

    related = [e for e in ledger.entries() if change_id in e.refs]
    handback = next((e for e in related if e.fact_type == "handback.result"), None)
    proposed = next((e for e in related if e.fact_type == "change.proposed"), None)
    changed = (handback.payload.get("derived_changes") if handback
               else (proposed.payload.get("paths") if proposed else [])) or []
    unit = unit_of(policy, changed)
    unit_name = unit["unit"] if unit else "repository"

    files: dict[str, str] = {}
    if unit:
        patterns = [glob_to_regex(g) for g in unit["paths"]]
        for path in sorted(code_root.rglob("*")):
            rel = path.relative_to(code_root).as_posix()
            if path.is_file() and "__pycache__" not in path.parts and any(
                    p.match(rel) for p in patterns):
                files[rel] = sha256_hex(path.read_bytes())

    approved = ledger.append(sign_submission(
        keydir, RELEASE_IDENTITY, "release.approved",
        {"subject": change_id, "gate_ref": gate.id, "tier": tier,
         "approvals": [e.id for e in approvals], "approvers": [e.actor for e in approvals],
         "launched_by": launcher}, [change_id, gate.id, *[e.id for e in approvals]]))

    number = 1 + sum(1 for e in ledger.find(fact_type="release.manifest")
                     if e.payload.get("unit") == unit_name)
    version = f"r{number}"
    usage = [u for e in related if e.fact_type == "gateway.usage" for u in e.payload["sessions"]]
    manifest = {
        "subject": f"{unit_name}@{version}", "unit": unit_name, "version": version,
        "change": change_id, "spec": gate.payload.get("spec"), "tier": tier,
        "files": files, "code_hash": hash_obj(sorted(files.items())),
        "skills": (proposed.payload.get("skills_given") if proposed else []) or [],
        "models": sorted({u["model"] for u in usage}),
        "live": bool(gate.payload.get("live", False)),
        "executor": gate.payload.get("executor"),
        "gate_ref": gate.id, "release_ref": approved.id,
        "approvals": [e.id for e in approvals],
        "evidence": [e.id for e in related
                     if e.fact_type in ("check.result", "tower.result", "handback.result",
                                        "tier.assigned", "evidence.mismatch")],
        "rulebook_hash": gate.payload.get("rulebook_hash"),
    }
    entry = ledger.append(sign_submission(
        keydir, RELEASE_IDENTITY, "release.manifest", manifest,
        [change_id, f"{unit_name}@{version}", *([manifest["spec"]] if manifest["spec"] else [])]))

    stored = None
    if unit:
        stored = releases_dir / unit_name / version
        for rel in files:
            target = stored / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(code_root / rel, target)
        (stored / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        # The channel head. New cases start on it. Running cases keep their pin.
        (releases_dir / unit_name / "channel.json").write_text(
            json.dumps({"current": version, "manifest_ref": entry.id}))
    return Release(unit_name, version, manifest, entry, stored)


def verify_stored(releases_dir: Path, unit: str, version: str, ledger: Ledger) -> list[str]:
    """Check a stored release against its sealed manifest. Empty means it matches."""
    sealed = next((e.payload for e in ledger.find(fact_type="release.manifest")
                   if e.payload.get("unit") == unit and e.payload.get("version") == version), None)
    if sealed is None:
        return [f"{unit}@{version} has no manifest in the ledger"]
    root = releases_dir / unit / version
    problems = []
    for rel, digest in sealed["files"].items():
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel} is missing from the stored release")
        elif sha256_hex(path.read_bytes()) != digest:
            problems.append(f"{rel} differs from the sealed manifest")
    on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*")
               if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
    for extra in sorted(on_disk - set(sealed["files"])):
        problems.append(f"{extra} is in the stored release and not in the manifest")
    return problems
