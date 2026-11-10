"""The journey: one road, from discovery to change, travelled once.

Every step runs the real mechanism and lands on one ledger. Nothing here is
narration: a step that says a release was refused has just tried the release
and been refused.

    discover -> decide -> specify -> build -> prove -> release -> run
             -> observe -> reproduce -> fix -> change reaches new cases -> account

Every model on the way is SCRIPTED: a deterministic stand-in, labelled. The
journey says so itself, at the start and at the end.

It lives here, at the edge, because it touches both programmes. The harness
never imports the runtime or an executor; this file may.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import executors as registry
from harness import chain, defect
from harness.knowledge import slice_tier, validate, verify
from harness.ledger import sign_submission
from harness.release import ReleaseRefused, approve, release
from harness.tower import make_tower
from runtime import evals
from runtime.vault import Vault

from . import world as W
from .scenario import load_scenario
from .suite import REVIEWER, Env, deliver, new_gateway, open_env

SKILL = "SKL-income-calculation"
NOT_LIVE = ("Every model on this journey is SCRIPTED: a deterministic program stands in for it. "
            "The controls around the model are real. Nothing here is evidence about a real "
            "model or coding agent.")


@dataclass
class Step:
    number: int
    stage: str
    title: str
    lines: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)


@dataclass
class Journey:
    steps: list[Step] = field(default_factory=list)
    env: Env | None = None
    totals: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"not_live": NOT_LIVE, "totals": self.totals,
                "steps": [s.__dict__ for s in self.steps]}


def run_journey(base_root: Path, keydir: Path, workdir: Path) -> Journey:
    journey = Journey()
    scenario = load_scenario(base_root, "pd-0002")
    env = open_env(scenario, base_root, workdir)
    journey.env = env
    ledger = env.ledger
    cases = {c["id"]: c for c in evals.catalogue(base_root)}
    vault = Vault(env.workdir / "vault")
    seen = [0]

    def step(stage: str, title: str) -> Step:
        s = Step(len(journey.steps) + 1, stage, title)
        journey.steps.append(s)
        return s

    def close(s: Step) -> None:
        """Attach every ledger entry written since the last step closed."""
        entries = ledger.entries()
        s.entries = [f"{e.id} {e.fact_type} by {e.actor}" for e in entries[seen[0]:]]
        seen[0] = len(entries)

    # 1 ---------------------------------------------------------------- discover
    s = step("discover", "A slice is chosen, with its outcome and what makes it regulated")
    item = env.knowledge.slices["SLICE-0001"]
    tier = slice_tier(item, env.knowledge, env.policy)
    problems = validate(env.knowledge, env.policy)
    s.lines += [
        f"{item['id']}: {item['title']}",
        f"outcome metric: {item['outcome_metric']}",
        f"moments: {', '.join(item['moments'])} (regulated)",
        f"obligations: {', '.join(item['obligations'])}; data classes: {', '.join(item['data_classes'])}",
        f"expected tier, derived from the moments, not chosen: Tier {tier}",
        f"every link in the knowledge layer resolves: {'yes' if not problems else problems[0]}",
    ]
    ledger.append(sign_submission(
        keydir, item["owner"], "slice.decided",
        {"subject": item["id"], "decision": item["decision"], "tier_expected": tier,
         "outcome_metric": item["outcome_metric"], "obligations": item["obligations"],
         "data_classes": item["data_classes"]}, [item["id"], *item["decisions"]]))
    s.lines.append(f"decision: {item['decision']}, recorded by {item['owner']}")
    close(s)

    # 2 ------------------------------------------------------------------ decide
    s = step("decide", "A product decision becomes a rule, a paired check, and a signed skill")
    skill = env.knowledge.skills[SKILL]
    released = verify(skill, env.policy)
    ledger.append(sign_submission(
        keydir, "skill-publisher", "skill.published",
        {"subject": skill.ref, "content_hash": released["content_hash"],
         "implements": skill.implements, "cards": skill.cards}, [skill.id, skill.ref, *skill.implements]))
    s.lines += [
        f"PD-0002: {env.knowledge.decisions['PD-0002']['title']}",
        f"RULE-0002: {env.knowledge.cards['RULE-0002']['title']}",
        f"{skill.ref} released by its owner, signature verified, content {released['content_hash'][:12]}",
        f"paired check {scenario.red_check} exists and is RED: the rule is not in the code yet",
    ]
    close(s)

    # 3 ----------------------------------------------------------------- specify
    s = step("specify", "A spec is approved: what may change, and which decision it implements")
    spec = env.knowledge.specs[scenario.spec]
    s.lines += [f"{spec.id} ({spec.status}): {spec.title}",
                f"cites {', '.join(spec.cites)}; may touch only {', '.join(spec.allowed_paths)}",
                "it states no tier: the rulebook will derive it from the change"]
    close(s)

    # 4, 5 -------------------------------------------------------- build, prove
    s = step("build", "An executor builds the change. The harness trusts none of its report")
    tower = make_tower(env.policy, env.knowledge)
    executor = registry.load("loop", env.scenario_dir, env.policy)
    gateway = new_gateway(env)
    order, run = deliver(env, scenario, executor, keydir, with_canary=True, gateway=gateway,
                         reviewer_model=REVIEWER, launched_by=W.LAUNCHER, tower=tower,
                         workdir=env.workdir / "build")
    s.lines += [f"executor: {executor.id} ({executor.kind}), model through the gateway: "
                f"{executor.model_id}",
                f"work order: a private worktree with no signing keys, no held-out cases, "
                f"{len(order.skills)} released skill",
                *[line.strip() for line in run.steps if "tower" not in line and "review" not in line
                  and "gate.result" not in line]]
    close(s)
    journey.steps[-1].entries = [e for e in journey.steps[-1].entries]

    s = step("prove", "The confidence tower: a number for every gate the tier requires")
    for result in run.tower:
        s.lines.append(f"{result.gate}: {result.number} against {result.threshold} "
                       f"({'pass' if result.passed else 'BLOCK'}) {result.detail}")
    s.lines += [line.strip() for line in run.steps if "review.advice" in line or "gate.result" in line]
    s.lines.append("the review is advice: the gate was already decided and reads nothing from it")
    s.entries = ["(recorded with the build step: tower.result, review.advice, gate.result)"]

    # 6 ----------------------------------------------------------------- release
    s = step("release", "Two people release it. The launcher cannot be one of them")
    approve(ledger, keydir, W.LAUNCHER, order.change_id)
    approve(ledger, keydir, "approver-ben", order.change_id)
    try:
        release(order.change_id, ledger=ledger, keydir=keydir, policy=env.policy,
                code_root=order.worktree, releases_dir=W.releases_dir(env))
        s.lines.append("RELEASED with the launcher's approval: this must not happen")
    except ReleaseRefused as exc:
        s.lines.append(f"first attempt, {W.LAUNCHER} and approver-ben: refused: {exc}")
    approve(ledger, keydir, "approver-cy", order.change_id)
    first = release(order.change_id, ledger=ledger, keydir=keydir, policy=env.policy,
                    code_root=order.worktree, releases_dir=W.releases_dir(env))
    s.lines += [
        f"second attempt, with approver-cy: released {first.unit}@{first.version}",
        f"sealed manifest: change {first.manifest['change']}, spec {first.manifest['spec']}, "
        f"Tier {first.manifest['tier']}, skill {first.manifest['skills'][0]['version']}, "
        f"{len(first.manifest['evidence'])} evidence entries, {len(first.manifest['approvals'])} approvals",
        f"the manifest says live={first.manifest['live']}: built by a scripted executor",
    ]
    close(s)

    # 7 --------------------------------------------------------------------- run
    s = step("run", "Cases run on the release. The figure comes from the decision service")
    engine = W.make_engine(env, keydir)

    def play(case_id: str, name: str | None = None) -> dict[str, Any]:
        engine.start(name or case_id, cases[case_id]["documents"], W.UNITS, W.MODELS)
        return engine.run(name or case_id)

    for case_id in ("SYN-002", "SYN-008"):
        state = play(case_id)
        figure = vault.get(state["result_ref"])["qualifying_monthly"]
        s.lines.append(f"{case_id} ({cases[case_id]['about']}): {state['status']}, decided by "
                       f"{state['decided_by']}, cards {', '.join(state['cards_applied'])}, "
                       f"matches the expected figure: {figure == cases[case_id]['expect']['qualifying']}")
    s.lines.append("SYN-008 used RULE-0002: the rule decided in step 2 is now deciding cases")
    waiting = play("SYN-004")
    s.lines.append(f"SYN-004 ({cases['SYN-004']['about']}): {waiting['status']}, routed under "
                   "PROC-0001, nothing calculated")
    result = evals.run_proc_0001(lambda: engine, list(cases.values()), ledger=ledger,
                                 keydir=keydir, models=W.MODELS)
    s.lines.append(f"eval {result.payload['subject']} for PROC-0001: {result.payload['outcome']}, "
                   f"{result.payload['cases']} cases including "
                   f"{result.payload['adversarial_twins']} adversarial twin")
    s.lines.append("the amounts are in the vault by reference; no ledger entry or checkpoint holds one")
    close(s)

    # 8 ----------------------------------------------------------------- observe
    s = step("observe", "Something goes wrong in production")
    halted = play("SYN-009")
    trace = [e for e in ledger.find(fact_type="case.halted") if "SYN-009" in e.refs][0]
    silent = play("SYN-010")
    s.lines += [
        f"SYN-009 ({cases['SYN-009']['about']}): {halted['status']}. {trace.payload['reason'][:110]}",
        f"SYN-010 ({cases['SYN-010']['about']}): {silent['status']}. The service ACCEPTED an "
        "infinite amount and returned a figure. Nothing halted. This is the worse fault, and "
        "only a person looking would have found it",
    ]
    close(s)

    # 9 --------------------------------------------------------------- reproduce
    s = step("reproduce", "The defect is opened and reproduced before anyone fixes it")
    defect.open_defect("DEF-0001", "The income service fails on, or accepts, a non-finite amount",
                       traces=[trace.id], unit=first.unit, version=first.version,
                       ledger=ledger, keydir=keydir)
    fix_scenario = load_scenario(base_root, "defect-0001")
    later = W.advance(env, fix_scenario, base_root, "main-after-release")
    fix_executor = registry.load("cli-stub", later.scenario_dir, later.policy)
    order2, blocked = deliver_without_reproduction(later, fix_scenario, fix_executor, keydir)
    s.lines.append(f"DEF-0001 opened, citing {trace.id}")
    s.lines.append("main moves on: the SME adds a permanent regression check "
                   f"({fix_scenario.red_check}), re-releases the skill as "
                   f"{later.knowledge.skills[SKILL].ref}, and SPEC-0010 is approved")
    s.lines.append(f"a fix is attempted first: {blocked.steps[0].replace('BLOCK: ', 'blocked: ')}")
    reproduced = defect.reproduce_against_release(
        "DEF-0001", later.knowledge.skills[SKILL], knowledge=later.knowledge, policy=later.policy,
        releases_dir=W.releases_dir(later), unit=first.unit, version=first.version,
        ledger=ledger, keydir=keydir)
    s.lines.append(f"reproduced against {reproduced.payload['against']}: "
                   f"{', '.join(reproduced.payload['failing_checks'])} fails and "
                   f"{reproduced.payload['heldout_failures']} held-out cases fail, as they must")
    close(s)

    # 10 -------------------------------------------------------------------- fix
    s = step("fix", "The fix takes the same road, built by a different executor")
    tower2 = make_tower(later.policy, later.knowledge)
    order2, run2 = deliver(later, fix_scenario, fix_executor, keydir, with_canary=True,
                           gateway=new_gateway(later), reviewer_model=REVIEWER,
                           launched_by=W.LAUNCHER, tower=tower2, workdir=later.workdir / "fix")
    for person in W.APPROVERS:
        approve(ledger, keydir, person, order2.change_id)
    second = release(order2.change_id, ledger=ledger, keydir=keydir, policy=later.policy,
                     code_root=order2.worktree, releases_dir=W.releases_dir(later))
    s.lines += [
        f"executor: {fix_executor.id} ({fix_executor.kind}), a different code path from the first; "
        "the control plane does not care",
        f"Tier {run2.tier}; checks: {sum(1 for v in run2.check_outcomes.values() if v == 'pass')} "
        f"of {len(run2.check_outcomes)} pass, including the new regression check",
        *[f"{t.gate}: {t.number} against {t.threshold}" for t in run2.tower],
        f"two approvals, released {second.unit}@{second.version}, built against skill "
        f"{second.manifest['skills'][0]['version']}",
    ]
    close(s)

    # 11 ---------------------------------------------------------------- change
    s = step("change", "The fix reaches new cases only")
    engine.resume("SYN-004", "case-reviewer-dee", cases["SYN-004"]["documents"][0])
    old = engine.run("SYN-004")
    after = play("SYN-010", "SYN-010-after-fix")
    s.lines += [
        f"SYN-004 was waiting for a person when {second.version} was released. Resolved by "
        f"case-reviewer-dee and decided by {old['decided_by']}: it kept the version it started with",
        f"a new case with the infinite amount, which {first.version} accepted: {after['status']} on "
        f"{engine.pins('SYN-010-after-fix')['decision-income']}, refused before any arithmetic",
    ]
    close(s)

    # 12 --------------------------------------------------------------- account
    s = step("account", "What the ledger can now answer, and what this did not show")
    erased = vault.erase("SYN-002")
    intact, reconciled, runtime_ok = ledger.verify(), ledger.reconcile(), engine.reconcile()
    ledger.append(sign_submission(
        keydir, "ci-reconciler", "reconciliation.result",
        {"subject": "reconciliation", "missing": [*reconciled, *runtime_ok],
         "complete": not reconciled and not runtime_ok, "chain_intact": not intact}, []))
    entries = ledger.entries()
    by_role = Counter(env.policy.identities[e.actor].role for e in entries)
    misplaced = [e.id for e in entries if not env.policy.may_write(e.actor, e.fact_type)]
    line_of_sight = chain.render_forward(
        chain.forward("PD-0002", later.knowledge, later.policy, ledger))
    s.lines += [
        f"SYN-002's personal data erased ({erased} values). The record of what happened is intact",
        f"ledger: {len(entries)} entries, chain and anchor "
        f"{'agree' if not intact else 'DISAGREE: ' + intact[0]}",
        f"completeness: {'every change has its gate result' if not reconciled else reconciled[0]}; "
        f"{'every checkpoint has its entry' if not runtime_ok else runtime_ok[0]}",
        f"{len(by_role)} roles wrote to it; entries written outside the writer's role: {len(misplaced)}",
        "line of sight, from the decision to the code to the evidence:",
        *["  " + line for line in line_of_sight.splitlines()],
        NOT_LIVE,
        "Also not shown: a real pipeline with branch protection, an operating-system sandbox, "
        "key custody, an object-locked anchor, Tier A auto-merge. See docs/limits.md.",
    ]
    close(s)

    journey.totals = {
        "entries": len(entries), "roles": dict(sorted(by_role.items())),
        "identities": len({e.actor for e in entries}),
        "fact_types": len({e.fact_type for e in entries}),
        "releases": [f"{first.unit}@{first.version}", f"{second.unit}@{second.version}"],
        "executors": [executor.id, fix_executor.id],
        "intact": not intact, "reconciled": not reconciled and not runtime_ok,
        "misplaced": len(misplaced), "live_models": 0,
    }
    return journey


def deliver_without_reproduction(env: Env, scenario, executor, keydir: Path):
    """Try to start the fix before the defect is reproduced. It must be blocked."""
    from harness.delivery import run_work_order
    from harness.workorder import build_work_order

    order = build_work_order(scenario.change, scenario.spec, executor=executor.identity,
                             root=env.root, policy=env.policy, knowledge=env.knowledge,
                             workdir=env.workdir / "fix-too-early")
    return order, run_work_order(order, executor, ledger=env.ledger, keydir=keydir,
                                 policy=env.policy, knowledge=env.knowledge)


def render(journey: Journey) -> str:
    out = ["THE JOURNEY: from discovery to change, on one ledger", NOT_LIVE, ""]
    for s in journey.steps:
        out.append(f"{s.number:>2}. {s.stage.upper()}: {s.title}")
        out += [f"      {line}" for line in s.lines]
        if s.entries:
            shown = s.entries if len(s.entries) <= 6 else s.entries[:5] + [
                f"... and {len(s.entries) - 5} more"]
            out.append("      on the ledger: " + "; ".join(shown))
        out.append("")
    t = journey.totals
    out.append(f"{t['entries']} ledger entries, {t['fact_types']} kinds of fact, written by "
               f"{t['identities']} identities in {len(t['roles'])} roles. Releases: "
               f"{', '.join(t['releases'])}. Live models: {t['live_models']}.")
    return "\n".join(out)
