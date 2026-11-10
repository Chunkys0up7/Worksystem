"""One change through the harness: propose, tier, check, gate.

Each step is written by a different identity. The executor proposes. CI tiers.
CI re-runs the checks. CI records the gate result. What the gate asks for
depends on the tier, which is how review effort follows risk.

Stages 4 and 5 add the spec link, the executor contract and the full
confidence tower. Tier C release needs two people; that approval step is not
built yet, so a Tier C change that passes its checks is HELD, never released.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checks import CI_GATES, record_results, run_skill_checks, skills_for_paths
from .gates import record_or_block
from .knowledge import Knowledge, SkillInvalid, verify
from .ledger import Ledger, LedgerRejected, LedgerUnavailable, sign_submission
from .policy import Policy
from .tiering import TierCheckFailed, classify

EXECUTOR = "agent-executor"
CI_TIERING = "ci-tiering"


@dataclass
class ChangeRun:
    change_id: str
    tier: str | None = None
    outcome: str = "blocked"
    steps: list[str] = field(default_factory=list)


def run_change(
    change_id: str,
    paths: list[str],
    *,
    ledger: Ledger,
    keydir: Path,
    policy: Policy,
    knowledge: Knowledge,
    obligations: list[str] | None = None,
) -> ChangeRun:
    run = ChangeRun(change_id)
    obligations = obligations or []

    # 1. The executor proposes. This is all the executor may write.
    try:
        entry = ledger.append(
            sign_submission(
                keydir, EXECUTOR, "change.proposed",
                {"subject": change_id, "paths": sorted(paths), "obligations": obligations},
                [change_id, *obligations],
            )
        )
        run.steps.append(f"{entry.id} change.proposed by {EXECUTOR}")
    except (LedgerRejected, LedgerUnavailable) as exc:
        run.steps.append(f"BLOCK: proposal could not be recorded ({exc})")
        return run

    # 2. CI sets the tier from the paths. The executor has no say.
    try:
        decision = classify(paths, policy, obligations)
    except TierCheckFailed as exc:
        # Every proposed change ends in a gate result, including a blocked one,
        # so reconciliation can tell "blocked" from "lost".
        why = f"tier check failed: {exc}"
        recorded = record_or_block(ledger, sign_submission(
            keydir, CI_GATES, "gate.result",
            {"subject": change_id, "outcome": "blocked", "why": why}, [change_id]), False)
        run.steps.append(
            f"{recorded.entry.id} gate.result blocked: {why}" if recorded.entry
            else f"BLOCK: {why}; and the gate result could not be recorded")
        return run
    try:
        entry = ledger.append(
            sign_submission(
                keydir, CI_TIERING, "tier.assigned",
                decision.to_payload(change_id), [change_id, *decision.obligations],
            )
        )
    except (LedgerRejected, LedgerUnavailable) as exc:
        run.steps.append(f"BLOCK: tier could not be recorded ({exc})")
        return run
    run.tier = decision.tier
    suffix = " (by default: unmapped paths)" if decision.by_default else ""
    run.steps.append(f"{entry.id} tier.assigned Tier {decision.tier}{suffix} by {CI_TIERING}")

    # 3. CI runs the paired checks of every skill that governs the change.
    skill_ids = skills_for_paths([f.path for f in decision.findings], policy)
    all_passed = True
    check_ids: list[str] = []
    for skill_id in skill_ids:
        skill = knowledge.skills.get(skill_id)
        if skill is None:
            run.steps.append(f"BLOCK: skill map names {skill_id}, which does not exist")
            all_passed = False
            continue
        try:
            release = verify(skill, policy)
            results = run_skill_checks(skill, knowledge, policy)
        except SkillInvalid as exc:
            run.steps.append(f"BLOCK: {exc}")
            all_passed = False
            continue
        outcomes = record_results(
            ledger, keydir, skill, results, policy, knowledge.root, release, change_id
        )
        for result, outcome in zip(results, outcomes):
            check_ids.append(result.check_id)
            run.steps.append(f"  {result.check_id} {outcome.reason}")
            all_passed = all_passed and outcome.passed
    if not skill_ids:
        run.steps.append("  no skill governs these paths, no paired checks to run")

    # 4. The gate. What it requires depends on the tier.
    if not all_passed:
        gate = "blocked"
        why = "a paired check failed or could not be evidenced"
    elif decision.tier == "C":
        gate = "held-for-approval"
        why = "checks pass; Tier C needs two-person release (approval step: stage 5)"
    else:
        gate = "passed"
        why = f"Tier {decision.tier} requirements met"
    submission = sign_submission(
        keydir, CI_GATES, "gate.result",
        {"subject": change_id, "tier": decision.tier, "outcome": gate,
         "why": why, "checks": check_ids, "rulebook_hash": decision.rulebook_hash},
        [change_id, *check_ids],
    )
    recorded = record_or_block(ledger, submission, gate == "passed")
    if recorded.entry is None:
        run.steps.append(recorded.reason)
        return run
    run.outcome = gate
    run.steps.append(f"{recorded.entry.id} gate.result {gate}: {why}")
    return run
