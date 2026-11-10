"""One work order through the harness, with an executor in the loop.

    propose -> run executor -> record its claim and what it used
            -> validate the hand-back -> tier -> re-run checks
            -> the confidence tower -> record mismatches -> advice -> gate

The executor writes two things, under its own identity: that it proposes a
change, and what it claims about it. Everything that decides the outcome is
derived by the harness from the worktree and written by CI identities. If the
executor's claim disagrees with what the harness derives, the difference is
recorded and the harness's version stands.

An executor that is not live (recorded, or driven by a scripted model) is
labelled in every entry it causes and in the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import review, trailers
from .canonical import sha256_hex
from .change import CI_TIERING
from .checks import CI_GATES, governing_skills, record_results, run_skill_checks
from .gateway import GATEWAY_IDENTITY
from .knowledge import Knowledge, SkillInvalid, verify
from .ledger import Ledger, LedgerRejected, LedgerUnavailable, sign_submission
from .policy import Policy
from .tiering import TierCheckFailed, classify
from .workorder import Executor, HandBack, Verdict, WorkOrder, validate_handback


@dataclass
class TowerResult:
    """One gate of the confidence tower. No number, no merge."""

    gate: str
    number: float | None
    threshold: float
    passed: bool
    detail: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {"gate": self.gate, "number": self.number, "threshold": self.threshold,
                "passed": self.passed, "detail": self.detail}


# (order, tier, changed paths) -> the tower results this tier requires
Tower = Callable[[WorkOrder, str, list[str]], list[TowerResult]]


@dataclass
class DeliveryRun:
    change_id: str
    tier: str | None = None
    outcome: str = "blocked"
    steps: list[str] = field(default_factory=list)
    verdict: Verdict | None = None
    mismatches: list[str] = field(default_factory=list)
    check_outcomes: dict[str, str] = field(default_factory=dict)
    tower: list[TowerResult] = field(default_factory=list)
    denied_attempts: list[dict[str, str]] = field(default_factory=list)
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
    tower: Tower | None = None,
) -> DeliveryRun:
    run = DeliveryRun(order.change_id)
    live = executor.kind == "live"
    tag = "" if live else f" ({executor.kind.upper()} executor, no live model ran)"
    common: dict[str, Any] = {
        "subject": order.change_id, "spec": order.spec_id, "executor": _describe(executor),
        "recorded": executor.kind == "recorded", "live": live}
    if order.launched_by:
        common["launched_by"] = order.launched_by
    refs = [order.change_id, order.spec_id]

    def record(identity: str, fact_type: str, payload: dict[str, Any], more=()):
        return ledger.append(sign_submission(
            keydir, identity, fact_type, {**common, **payload}, [*refs, *more]))

    def gate(outcome: str, why: str, **extra: Any) -> DeliveryRun:
        """Every proposed change ends in a gate result, including a blocked one."""
        try:
            entry = record(CI_GATES, "gate.result", {"outcome": outcome, "why": why, **extra},
                           extra.get("checks", []))
            run.outcome = outcome
            run.steps.append(f"{entry.id} gate.result {outcome}: {why}{tag}")
        except (LedgerRejected, LedgerUnavailable) as exc:
            run.outcome = "blocked"
            run.steps.append(f"BLOCK: gate result could not be recorded ({exc})")
        return run

    def usage() -> None:
        """What the executor and reviewer used, from the gateway's own count."""
        if order.gateway is None or not (order.gateway.sessions or order.gateway.refusals):
            return
        try:
            entry = record(GATEWAY_IDENTITY, "gateway.usage",
                           {"sessions": order.gateway.usage(),
                            "refusals": list(order.gateway.refusals)})
            run.steps.append(f"{entry.id} gateway.usage: " + (", ".join(
                f"{u['model']} ({u['kind']}) {u['calls']} calls" for u in order.gateway.usage())
                or "no session opened") + (
                f"; refused: {'; '.join(order.gateway.refusals)}" if order.gateway.refusals else ""))
        except (LedgerRejected, LedgerUnavailable):
            pass  # the gate result below still records the outcome

    if executor.identity != order.executor:
        run.steps.append(
            f"BLOCK: work order is for {order.executor}, executor signs as {executor.identity}")
        return run

    # 1. The executor proposes, under its own identity.
    try:
        entry = record(executor.identity, "change.proposed",
                       {"skills_given": [s.as_dict() for s in order.skills]})
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
        usage()
        return gate("blocked", f"executor failed: {type(exc).__name__}: {exc}")
    if order.tools is not None:
        run.denied_attempts = list(order.tools.attempts)

    # 3. Its claim is recorded as a claim.
    try:
        entry = record(executor.identity, "executor.evidence",
                       {"claim": handback.evidence,
                        "transcript_hash": sha256_hex(handback.transcript.encode())})
        run.steps.append(f"{entry.id} executor.evidence (a claim) by {executor.identity}")
    except (LedgerRejected, LedgerUnavailable) as exc:
        usage()
        return gate("blocked", f"executor evidence could not be recorded ({exc})")

    # 4. The hand-back is validated from the worktree, not from the claim.
    verdict = validate_handback(order, handback)
    run.verdict = verdict
    try:
        entry = record(CI_GATES, "handback.result",
                       {"accepted": verdict.accepted, "derived_changes": verdict.changed,
                        "violations": verdict.violations,
                        "denied_attempts": run.denied_attempts})
    except (LedgerRejected, LedgerUnavailable) as exc:
        usage()
        return gate("blocked", f"hand-back verdict could not be recorded ({exc})")
    for attempt in run.denied_attempts:
        run.steps.append(
            f"    refused before it happened: {attempt['action']} {attempt['path']} "
            f"({attempt['why']})")
    if not verdict.accepted:
        run.steps.append(f"{entry.id} handback.result REJECTED")
        for violation in verdict.violations:
            run.steps.append(f"    {violation}")
        usage()
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
        usage()
        return gate("blocked", f"tier check failed: {exc}")
    except (LedgerRejected, LedgerUnavailable) as exc:
        usage()
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

    # 7. The confidence tower: the gates this tier requires, each with a number.
    if tower is not None and all_passed:
        for result in tower(order, decision.tier, verdict.changed):
            run.tower.append(result)
            try:
                entry = record(CI_GATES, "tower.result", result.as_payload())
                mark = "PASS" if result.passed else "BLOCK"
                run.steps.append(
                    f"  {entry.id} tower {result.gate}: {result.number} against "
                    f"{result.threshold} {mark}" + (f" ({result.detail})" if result.detail else ""))
            except (LedgerRejected, LedgerUnavailable) as exc:
                run.steps.append(f"  BLOCK: tower result could not be recorded ({exc})")
                result.passed = False
            all_passed = all_passed and result.passed and result.number is not None

    # 8. Where the claim and the derived evidence differ, say so. The claim loses.
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
    if order.gateway is not None:
        used = sorted({u["model"] for u in order.gateway.usage() if u["role"] == "author"})
        said = handback.evidence.get("model")
        if used and said not in used:
            mismatches.append(f"model: executor claimed {said!r}, the gateway served {used}")
    run.mismatches = mismatches
    if mismatches:
        try:
            entry = record(CI_GATES, "evidence.mismatch", {"mismatches": mismatches})
            run.steps.append(f"{entry.id} evidence.mismatch: the claim differs from what CI derived")
            for mismatch in mismatches:
                run.steps.append(f"    {mismatch}")
        except (LedgerRejected, LedgerUnavailable) as exc:
            usage()
            return gate("blocked", f"mismatch could not be recorded ({exc})")

    # 9. Advisory review, by a model that did not write the change. The gate
    #    below is already decided by `all_passed` and reads nothing from it.
    if order.gateway is not None and order.reviewer_model:
        families = {u["family"] for u in order.gateway.usage() if u["role"] == "author"}
        changed = {p: (order.worktree / p).read_text()
                   for p in verdict.changed if (order.worktree / p).is_file()}
        run.steps.append("  " + review.advise(
            change_id=order.change_id, spec_id=order.spec_id, changed=changed,
            author_families=families, reviewer_model=order.reviewer_model,
            gateway=order.gateway, ledger=ledger, keydir=keydir))
    usage()

    # 10. The gate.
    checks = sorted(run.check_outcomes)
    extra = {"tier": decision.tier, "checks": checks, "rulebook_hash": decision.rulebook_hash,
             "tower": [t.as_payload() for t in run.tower]}
    if not all_passed:
        return gate("blocked", "a check or a tower gate failed or could not be evidenced", **extra)
    run.trailers = trailers.render(order, executor)
    if decision.tier == "C":
        return gate("held-for-approval",
                    "all gates pass; Tier C needs two-person release", **extra)
    return gate("passed", f"Tier {decision.tier} requirements met", **extra)
