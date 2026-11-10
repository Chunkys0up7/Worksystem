"""Harness-degraded mode: what happens when the pipeline or the ledger is down.

Every change goes through the harness, including an urgent one. So the harness
being unavailable needs a defined path, or the first outage produces an
improvised one. This is it:

- a fixed subset of gates (policy/tower.yaml), run by hand: the paired checks
  and the fitness functions. Both must pass. Nothing is waived.
- two named people, different, who each sign the record
- the record goes to a spool, outside the ledger, with a back-fill due date
- when the ledger is back, the record is back-filled, marked as degraded, and
  marked late if the window was missed
- anything still in the spool past its date is reported

It is drilled (`python -m harness degraded-drill`), not left for the day it is
needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import identity
from .canonical import hash_obj
from .checks import governing_skills, run_skill_checks
from .knowledge import Knowledge
from .ledger import Entry, Ledger, sign_submission
from .policy import Policy
from .release import RELEASE_IDENTITY
from .tower import fitness


class DegradedRefused(Exception):
    pass


def _body(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k != "approvals"}


def open_degraded(change_id: str, reason: str, changed: list[str], *, code_root: Path,
                  policy: Policy, knowledge: Knowledge, keydir: Path, approvers: list[str],
                  spool: Path, now: datetime) -> Path:
    rules = policy.tower.get("degraded", {})
    need = int(rules.get("approvals_required", 2))
    people = [a for a in dict.fromkeys(approvers)
              if a in policy.identities and policy.identities[a].role == "approver"]
    if len(people) < need:
        raise DegradedRefused(f"degraded mode needs {need} different approvers, got {len(people)}")

    gates: dict[str, Any] = {}
    if "paired-checks" in rules.get("gates", []):
        failed, ran = [], 0
        for skill_id in governing_skills(changed, policy):
            for result in run_skill_checks(knowledge.skills[skill_id], knowledge, policy,
                                           code_root=code_root):
                ran += 1
                if not result.passed:
                    failed.append(result.check_id)
        gates["paired-checks"] = {"ran": ran, "failed": failed}
        if failed:
            raise DegradedRefused(f"paired checks failed: {', '.join(failed)}. Nothing is waived.")
    if "fitness" in rules.get("gates", []):
        violations = fitness.run(code_root, policy.fitness_rules)
        gates["fitness"] = {"violations": len(violations)}
        if violations:
            raise DegradedRefused(f"fitness violations: {violations[0]}. Nothing is waived.")

    record: dict[str, Any] = {
        "change": change_id, "reason": reason, "changed": sorted(changed), "gates": gates,
        "opened_at": now.isoformat(),
        "backfill_due": (now + timedelta(days=int(rules.get("backfill_days", 3)))).isoformat(),
    }
    digest = hash_obj(record)
    record["approvals"] = [
        {"approver": person, "signature": identity.sign(keydir, person, digest.encode())}
        for person in people[:need]]
    spool.mkdir(parents=True, exist_ok=True)
    path = spool / f"{change_id}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True))
    return path


def backfill(path: Path, *, ledger: Ledger, keydir: Path, policy: Policy,
             now: datetime) -> Entry:
    record = json.loads(path.read_text())
    digest = hash_obj(_body(record))
    need = int(policy.tower.get("degraded", {}).get("approvals_required", 2))
    valid: list[str] = []
    for approval in record.get("approvals", []):
        ident = policy.identities.get(approval.get("approver", ""))
        if (ident and ident.role == "approver" and ident.name not in valid
                and identity.verify(ident.public_key, digest.encode(),
                                    approval.get("signature", ""))):
            valid.append(ident.name)
    if len(valid) < need:
        raise DegradedRefused(
            f"{path.name}: {len(valid)} of {need} valid approver signatures; the record "
            "was altered or was never properly approved")
    late = now > datetime.fromisoformat(record["backfill_due"])
    entry = ledger.append(sign_submission(
        keydir, RELEASE_IDENTITY, "degraded.backfilled",
        {"subject": record["change"], "degraded": True, "reason": record["reason"],
         "changed": record["changed"], "gates": record["gates"],
         "opened_at": record["opened_at"], "backfilled_at": now.isoformat(), "late": late,
         "record_hash": digest, "approvers": valid}, [record["change"]]))
    done = path.parent / "done"
    done.mkdir(exist_ok=True)
    path.replace(done / path.name)
    return entry


def overdue(spool: Path, now: datetime) -> list[str]:
    """Degraded changes not yet back-filled, past their due date."""
    out = []
    for path in sorted(spool.glob("*.json")):
        record = json.loads(path.read_text())
        if now > datetime.fromisoformat(record["backfill_due"]):
            out.append(f"{record['change']}: back-fill was due {record['backfill_due']}")
    return out
