"""One work order through the harness, with an executor in the loop.

    propose -> run executor -> record its claim -> validate the hand-back
            -> tier -> re-run checks -> record mismatches -> gate

The executor writes two things, under its own identity: that it proposes a
change, and what it claims about it. Everything that decides the outcome is
derived by the harness from the worktree and written by CI identities. If the
executor's claim disagrees with what the harness derives, the difference is
recorded and the harness's version stands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import trailers
from .canonical import sha256_hex
from .change import CI_TIERING
from .checks import CI_GATES, governing_skills, record_results, run_skill_checks
from .knowledge import Knowledge, SkillInvalid, verify
from .ledger import Ledger, LedgerRejected, LedgerUnavailable, sign_submission
from .policy import Policy
from .tiering import TierCheckFailed, classify
from .workorder import Executor, HandBack, Verdict, WorkOrder, validate_handback


@dataclass
class DeliveryRun:
    change_id: str
    tier: str | None = None
    outcome: str = "blocked"
    steps: list[str] = field(default_factory=list)
    verdict: Verdict | None = None
    mismatches: list[str] = field(default_factory=list)
    check_outcomes: dict[str, str] = field(default_factory=dict)
    trailers: str = ""


def _describe(executor: Executor) -> dict[str, Any]:
    return {"id": executor.id, "kind": executor.kind, "version": executor.version}


def run_work_order(
    order: WorkOrder,
    executor: Executor,
    *,
    ledger: Ledger,
    keydir: Path,
    policy: Policy,
    knowledge: Knowledge,
) -> DeliveryRun:
    run = DeliveryRun(order.change_id)
    recorded = executor.kind == "recorded"
    tag = " (RECORDED executor, no model ran)" if recorded else ""
    common = {"subject": order.change_id, "spec": order.spec_id,
              "executor": _describe(executor), "recorded": recorded}
    refs = [order.change_id, order.spec_id]

    def gate(outcome: str, why: str, **extra: Any) -> DeliveryRun:
        """Every proposed change ends in a gate result, including a blocked one."""
        try:
            entry = ledger.append(sign_submission(
                keydir, CI_GATES, "gate.result",
                {**common, "outcome": outcome, "why": why, **extra},
                [*refs, *extra.get("checks", [])]))
            run.outcome = outcome
            run.steps.append(f"{entry.id} gate.result {outcome}: {why}{tag}")
        except (LedgerRejected, LedgerUnavailable) as exc:
            run.outcome = "blocked"
            run.steps.append(f"BLOCK: gate result could not be recorded ({exc})")
        return run

    if executor.identity != order.executor:
        run.steps.append(
            f"BLOCK: work order is for {order.executor}, executor signs as {executor.identity}")
        return run

    # 1. The executor proposes, under its own identity.
    try:
        entry = ledger.append(sign_submission(
            keydir, executor.identity, "change.proposed",
            {**common, "skills_given": [s.as_dict() for s in order.skills]}, refs))
        run.steps.append(f"{entry.id} change.proposed by {executor.identity}{tag}")
    except (LedgerRejected, LedgerUnavailable) as exc:
        run.steps.append(f"BLOCK: proposal could not be recorded ({exc})")
        return run

    # 2. The executor works in its worktree.
    try:
        handback = executor.run(order)
        if not isinstance(handback, HandBack):
            raise TypeError("the executor did not return a hand-back")
    except Exception as exc:  # an executor that fails has delivered nothing
        return gate("blocked", f"executor failed: {type(exc).__name__}: {exc}")

    # 3. Its claim is recorded as a claim.
    try:
        entry = ledger.append(sign_submission(
            keydir, executor.identity, "executor.evidence",
            {**common, "claim": handback.evidence,
             "transcript_hash": sha256_hex(handback.transcript.encode())}, refs))
        run.steps.append(f"{entry.id} executor.evidence (a claim) by {executor.identity}")
    except (LedgerRejected, LedgerUnavailable) as exc:
        return gate("blocked", f"executor evidence could not be recorded ({exc})")

    # 4. The hand-back is validated from the worktree, not from the claim.
    verdict = validate_handback(order, handback)
    run.verdict = verdict
    try:
        entry = ledger.append(sign_submission(
            keydir, CI_GATES, "handback.result",
            {**common, "accepted": verdict.accepted, "derived_changes": verdict.changed,
             "violations": verdict.violations}, refs))
    except (LedgerRejected, LedgerUnavailable) as exc:
        return gate("blocked", f"hand-back verdict could not be recorded ({exc})")
    if not verdict.accepted:
        run.steps.append(f"{entry.id} handback.result REJECTED")
        for violation in verdict.violations:
            run.steps.append(f"    {violation}")
        return gate("blocked", "hand-back rejected: " + "; ".join(verdict.violations))
    run.steps.append(
        f"{entry.id} handback.result accepted, derived changes: {', '.join(verdict.changed)}")

    # 5. Tier, from the derived changes.
    try:
        decision = classify(verdict.changed, policy,
                            [c for c in knowledge.specs[order.spec_id].cites
                             if c.startswith("OBL-")])
        entry = ledger.append(sign_submission(
            keydir, CI_TIERING, "tier.assigned",
            decision.to_payload(order.change_id), [*refs, *decision.obligations]))
    except TierCheckFailed as exc:
        return gate("blocked", f"tier check failed: {exc}")
    except (LedgerRejected, LedgerUnavailable) as exc:
        return gate("blocked", f"tier could not be recorded ({exc})")
    run.tier = decision.tier
    run.steps.append(f"{entry.id} tier.assigned Tier {decision.tier} by {CI_TIERING}")

    # 6. CI re-runs the paired checks: released skill, worktree code.
    all_passed = True
    for skill_id in governing_skills(verdict.changed, policy):
        skill = knowledge.skills[skill_id]
        try:
            release = verify(skill, policy)
            results = run_skill_checks(skill, knowledge, policy, code_root=order.worktree)
        except SkillInvalid as exc:
            run.steps.append(f"  BLOCK: {exc}")
            all_passed = False
            continue
        outcomes = record_results(ledger, keydir, skill, results, policy, order.worktree,
                                  release, order.change_id, extra_refs=(order.spec_id,))
        for result, outcome in zip(results, outcomes):
            run.check_outcomes[result.check_id] = "pass" if result.passed else "fail"
            run.steps.append(f"  {result.check_id} {outcome.reason}")
            all_passed = all_passed and outcome.passed

    # 7. Where the claim and the derived evidence differ, say so. The claim loses.
    mismatches = list(verdict.mismatches)
    claimed = handback.evidence.get("checks_claimed") or {}
    if isinstance(claimed, dict):
        for check_id, value in sorted(claimed.items()):
            actual = run.check_outcomes.get(check_id)
            if actual != value:
                mismatches.append(
                    f"{check_id}: executor claimed {value}, CI got {actual or 'no such check'}")
    for key in ("tier", "gate", "skip_checks"):
        if key in handback.evidence:
            mismatches.append(
                f"executor stated {key}={handback.evidence[key]!r}; that is not its to state")
    run.mismatches = mismatches
    if mismatches:
        try:
            entry = ledger.append(sign_submission(
                keydir, CI_GATES, "evidence.mismatch", {**common, "mismatches": mismatches}, refs))
            run.steps.append(f"{entry.id} evidence.mismatch: the claim differs from what CI derived")
            for mismatch in mismatches:
                run.steps.append(f"    {mismatch}")
        except (LedgerRejected, LedgerUnavailable) as exc:
            return gate("blocked", f"mismatch could not be recorded ({exc})")

    # 8. The gate.
    checks = sorted(run.check_outcomes)
    extra = {"tier": decision.tier, "checks": checks, "rulebook_hash": decision.rulebook_hash}
    if not all_passed:
        return gate("blocked", "a paired check failed or could not be evidenced", **extra)
    run.trailers = trailers.render(order, executor)
    if decision.tier == "C":
        return gate("held-for-approval",
                    "checks pass; Tier C needs two-person release (approval step: stage 5)",
                    **extra)
    return gate("passed", f"Tier {decision.tier} requirements met", **extra)
