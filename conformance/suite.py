"""The executor conformance suite. One suite, any adapter.

It prepares a scenario, hands the adapter a work order, runs the hand-back
through the harness, and then judges each clause of the contract from what the
harness recorded. Nothing is judged from the executor's own report.

A clause has three possible results:

    pass   shown, here, for this adapter
    fail   shown not to hold
    n/a    cannot be shown for this adapter, with the reason

"n/a" is not a pass, and the summary counts it apart. An executor that is not
live (recorded, or driven by a scripted model) is judged on the harness side of
the contract only, and the verdict line says so. This suite never calls a
recorded or scripted executor conformant without that qualifier.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import executors as registry
from executors.models import ScriptedAuthor, scripted_adapters
from harness import chain, trailers
from harness.checks import governing_skills, run_skill_checks
from harness.delivery import DeliveryRun, Tower, run_work_order
from harness.gateway import Gateway
from harness.knowledge import load_knowledge, validate
from harness.ledger import FileAnchor, Ledger, sign_submission
from harness.ledger.store import SqliteStore
from harness.policy import load_policy
from harness.tiering.rulebook import glob_to_regex
from harness.workorder import WorkOrder, build_work_order, snapshot

from .scenario import Scenario, prepare

CONFORMANCE = "ci-conformance"
REVIEWER = "scripted-reviewer"
CONTROL_ROLES = ("gates", "tiering")


@dataclass
class Clause:
    criterion: str
    name: str
    status: str
    detail: str


@dataclass
class Env:
    """A prepared scenario: an overlaid repository and an empty ledger."""

    root: Path
    workdir: Path
    policy: Any
    knowledge: Any
    ledger: Ledger
    scenario_dir: Path


@dataclass
class SuiteRun:
    executor: str
    kind: str
    scenario: str
    clauses: list[Clause] = field(default_factory=list)
    delivery: DeliveryRun | None = None
    ledger: Ledger | None = None
    order: WorkOrder | None = None
    root: Path | None = None

    def count(self, status: str) -> int:
        return sum(1 for c in self.clauses if c.status == status)

    @property
    def verdict(self) -> str:
        if self.count("fail"):
            return "does not conform"
        judged = f"passes {self.count('pass')} of {len(self.clauses)} clauses"
        if self.count("n/a"):
            judged += f"; {self.count('n/a')} could not be judged for this executor"
        if self.kind != "live":
            return f"{judged}. {self.kind.upper()}: the harness side only, no live model was judged"
        return "conforms" if not self.count("n/a") else judged


def open_env(scenario: Scenario, base_root: Path, workdir: Path) -> Env:
    root = prepare(scenario, base_root, workdir / "repo")
    policy, knowledge = load_policy(root), load_knowledge(root)
    store = SqliteStore(workdir / "ledger" / "ledger.db")
    store.init()
    ledger = Ledger(store, policy, FileAnchor(workdir / "ledger" / "anchor.json"))
    return Env(root, workdir, policy, knowledge, ledger,
               root / "conformance" / "scenarios" / scenario.id)


def new_gateway(env: Env, adapters: dict[str, Any] | None = None, caps=None) -> Gateway:
    return Gateway(env.policy, {**scripted_adapters(env.scenario_dir), **(adapters or {})},
                   caps=caps)


def deliver(env: Env, scenario: Scenario, executor, keydir: Path, *,
            with_canary: bool = False, gateway: Gateway | None = None,
            reviewer_model: str | None = None, launched_by: str | None = None,
            tower: Tower | None = None, change_id: str | None = None,
            workdir: Path | None = None) -> tuple[WorkOrder, DeliveryRun]:
    """Build the scenario's work order and run it through the harness."""
    order = build_work_order(
        change_id or scenario.change, scenario.spec, executor=executor.identity, root=env.root,
        policy=env.policy, knowledge=env.knowledge, workdir=workdir or env.workdir,
        with_canary=with_canary, gateway=gateway, reviewer_model=reviewer_model,
        launched_by=launched_by)
    run = run_work_order(order, executor, ledger=env.ledger, keydir=keydir,
                         policy=env.policy, knowledge=env.knowledge, tower=tower)
    return order, run


def control_shape(env: Env) -> list[tuple]:
    """What the control plane recorded, with everything executor-specific removed.

    Two executors are interchangeable if this is identical for both: same facts,
    in the same order, from the same roles, with the same outcomes."""
    shape = []
    for entry in env.ledger.entries():
        role = env.policy.identities[entry.actor].role
        if role not in CONTROL_ROLES:
            continue
        p = entry.payload
        shape.append((entry.fact_type, role, p.get("accepted"), p.get("tier"), p.get("outcome"),
                      p.get("subject") if entry.fact_type == "check.result" else None,
                      p.get("gate"), p.get("passed")))
    return shape


def run_suite(
    name: str,
    scenario: Scenario,
    base_root: Path,
    keydir: Path,
    workdir: Path,
    *,
    make_executor: Callable[[Env], Any] | None = None,
    swap_with: str | None = None,
    second_model: str | None = None,
    tower_factory: Callable[[Env], Tower] | None = None,
) -> SuiteRun:
    def build(env: Env, which: str = name):
        if make_executor is not None and which == name:
            return make_executor(env)
        return registry.load(which, env.scenario_dir, env.policy)

    def tower_of(env: Env) -> Tower | None:
        return tower_factory(env) if tower_factory else None

    env = open_env(scenario, base_root, workdir / "main")
    root, policy, knowledge, ledger = env.root, env.policy, env.knowledge, env.ledger
    executor = build(env)
    kind = executor.kind
    run = SuiteRun(executor.id, kind, scenario.id, root=root, ledger=ledger)

    def add(criterion: str, clause: str, ok: bool | None, detail: str) -> None:
        status = "n/a" if ok is None else ("pass" if ok else "fail")
        run.clauses.append(Clause(criterion, clause, status, detail))

    def role_of(entry) -> str:
        return policy.identities[entry.actor].role

    problems = validate(knowledge, policy)
    add("setup", "scenario is whole", not problems,
        "; ".join(problems) or "every link in the overlaid repository resolves")
    if problems:
        return run

    base_before = snapshot(root)
    gateway = new_gateway(env)
    order = build_work_order(
        scenario.change, scenario.spec, executor=executor.identity, root=root, policy=policy,
        knowledge=knowledge, workdir=env.workdir, with_canary=kind != "recorded",
        gateway=gateway, reviewer_model=REVIEWER, launched_by="approver-ana")
    run.order = order

    # The scenario must start red, or passing it means nothing.
    in_scope = [p for p in order.manifest
                if any(glob_to_regex(g).match(p) for g in order.allowed_paths)]
    red = {}
    for skill_id in governing_skills(in_scope, policy):
        for result in run_skill_checks(knowledge.skills[skill_id], knowledge, policy,
                                       code_root=order.worktree):
            red[result.check_id] = result.passed
    add("setup", "starts red", red.get(scenario.red_check) is False,
        f"{scenario.red_check} before the change: "
        f"{'fails, as it should' if red.get(scenario.red_check) is False else 'does not fail'}")

    delivery = run_work_order(order, executor, ledger=ledger, keydir=keydir, policy=policy,
                              knowledge=knowledge, tower=tower_of(env))
    run.delivery = delivery
    entries = ledger.entries()
    verdict = delivery.verdict
    claim = next((e.payload["claim"] for e in entries if e.fact_type == "executor.evidence"), {})

    # A2
    given = sorted((s.id, s.version, s.content_hash) for s in order.skills)
    claimed = sorted((s.get("id"), s.get("version"), s.get("content_hash"))
                     for s in claim.get("skills_loaded") or [] if isinstance(s, dict))
    add("A2", "skill-map load", bool(given) and given == claimed,
        f"work order gave {[f'{g[0]}@{g[1]}' for g in given]}; the evidence lists the same, "
        "with matching content hashes" if given == claimed else f"given {given}, claimed {claimed}")

    # A3
    if kind == "recorded":
        add("A3", "skill text reached the executor", None,
            "a recording cannot read a one-time token")
    else:
        add("A3", "skill text reached the executor", claim.get("canary") == order.canary,
            "the one-time token placed in the skill was returned; that shows delivery, not use"
            if claim.get("canary") == order.canary else "the one-time token was not returned")

    # A4
    leaked = [p for p in order.manifest
              if any(glob_to_regex(g).match(p) for g in policy.deny_read)]
    clean = not leaked and base_before == snapshot(root)
    add("A4", "deny list holds from outside", clean,
        "nothing on the read deny list was in the worktree; the base repository is "
        "byte-identical after the run" if clean else f"leaked {leaked[:3]} or base changed")

    # A5
    add("A5", "hand-back validated from the diff", bool(verdict) and verdict.accepted,
        f"harness derived {verdict.changed}, all inside the spec's allowed paths"
        if verdict and verdict.accepted
        else "; ".join(verdict.violations) if verdict else "no verdict")

    # A6
    results = [e for e in entries if e.fact_type == "check.result"]
    gate = next((e for e in entries if e.fact_type == "gate.result"), None)
    add("A6", "evidence re-derived by CI",
        bool(results) and all(role_of(e) == "gates" for e in results)
        and gate is not None and role_of(gate) == "gates",
        f"{len(results)} check results and the gate were written by the gates role from the "
        f"worktree; the executor wrote only "
        f"{sorted({e.fact_type for e in entries if role_of(e) == 'executor'})}")

    # A7
    message = f"{scenario.title}\n\n{delivery.trailers}\n"
    trailer_problems = (trailers.check(message, verdict.changed if verdict else [],
                                       knowledge, policy)
                        if delivery.trailers else ["no trailers were produced"])
    add("A7", "change trailers", not trailer_problems,
        "; ".join(trailer_problems) or delivery.trailers.replace("\n", " | "))

    # A8
    calls_gateway = any(u["role"] == "author" for u in gateway.usage())
    if second_model is None or not calls_gateway:
        add("A8", "model portability", None,
            "no second model was given" if calls_gateway
            else "this executor takes no model through the gateway")
    else:
        env2 = open_env(scenario, base_root, workdir / "model2")
        other = registry.load(second_model, env2.scenario_dir, env2.policy)
        _, second = deliver(env2, scenario, other, keydir, with_canary=True,
                            gateway=new_gateway(env2), reviewer_model=REVIEWER,
                            tower=tower_of(env2))
        same = control_shape(env) == control_shape(env2) and second.outcome == delivery.outcome
        families = sorted({policy.models[executor.model_id]["family"],
                           policy.models[other.model_id]["family"]})
        if not same:
            add("A8", "model portability", False, "the two runs differ")
        elif kind != "live":
            add("A8", "model portability", None,
                f"ran on two scripted models ({', '.join(families)}) with identical gates and "
                "evidence. That shows the harness does not care which model answered. It is "
                "not evidence about real models, so it is not counted as a pass")
        else:
            add("A8", "model portability", True,
                f"two model families ({', '.join(families)}): gates and evidence identical")

    # A9
    if swap_with is None:
        add("A9", "executor swap drill", None, "no second executor was given")
    else:
        env3 = open_env(scenario, base_root, workdir / "swap")
        other = registry.load(swap_with, env3.scenario_dir, env3.policy)
        _, swapped = deliver(env3, scenario, other, keydir, with_canary=other.kind != "recorded",
                             gateway=new_gateway(env3), reviewer_model=REVIEWER,
                             tower=tower_of(env3))
        same = control_shape(env) == control_shape(env3) and swapped.outcome == delivery.outcome
        add("A9", "executor swap drill", same,
            f"'{executor.id}' and '{other.id}' ({other.kind}) are different code paths. Tier, "
            f"hand-back verdict, check results, tower and gate are identical: "
            f"{len(control_shape(env))} control facts each, zero differences"
            if same else f"control facts differ between '{executor.id}' and '{other.id}'")

    # A10
    advice = [e for e in entries if e.fact_type == "review.advice"]
    author_families = {u["family"] for u in gateway.usage() if u["role"] == "author"}
    ok = (len(advice) == 1 and role_of(advice[0]) == "reviewer"
          and advice[0].payload.get("binding") is False
          and gateway.family_of(REVIEWER) not in author_families
          and "advice" not in json.dumps(gate.payload if gate else {}))
    add("A10", "review is advice only", ok,
        f"{advice[0].id} was written by the reviewer role, from a different model family, "
        "marked non-binding; the gate result carries no trace of it" if ok
        else "no advice was recorded, or it was not separate from the author or the gate")

    # A11
    if not calls_gateway:
        add("A11", "budget cap fails closed", None,
            "this executor does not call a model through the gateway")
    else:
        env4 = open_env(scenario, base_root, workdir / "budget")
        runaway = ScriptedAuthor(executor.model_id, Path("unused"), runaway=True)
        capped = new_gateway(env4, {executor.model_id: runaway}, caps={"max_calls": 5})
        _, spent = deliver(env4, scenario, build(env4), keydir, gateway=capped)
        why = env4.ledger.find(fact_type="gate.result")[-1].payload["why"]
        used = env4.ledger.find(fact_type="gateway.usage")
        ok = spent.outcome == "blocked" and "BudgetExceeded" in why and len(used) == 1
        add("A11", "budget cap fails closed", ok,
            f"a model that never stops was cut off at the cap "
            f"({used[0].payload['sessions'][0]['calls']} calls); the change was blocked and "
            "the usage recorded" if ok else why)

    # A12
    data = chain.forward(scenario.decision, knowledge, policy, ledger)
    specs = data["specs"]
    changes = [c for s in specs for c in s["changes"]]
    checks = [c for s in data["skills"] for c in s["checks"]]
    closed = (bool(specs) and bool(changes)
              and all(c["latest"] and c["latest"]["outcome"] == "pass" for c in checks)
              and any(c["check"] == scenario.red_check for c in checks))
    add("A12", "the ID chain closes", closed,
        f"{scenario.decision} -> {[s['skill'] for s in data['skills']]} -> "
        f"{[s['spec'] for s in specs]} -> {[c['change'] for c in changes]} -> "
        f"{len(checks)} checks passing in the ledger" if closed else f"open: {data.get('open')}")

    # A13
    if kind == "live":
        add("A13", "a non-live executor is labelled", None, "this executor is live")
    else:
        flagged = [e for e in entries if e.payload.get("live") is False]
        labelled = (
            {"change.proposed", "executor.evidence", "handback.result", "gate.result"}
            <= {e.fact_type for e in flagged}
            and f"({kind})" in delivery.trailers
            and any(kind.upper() in s for s in delivery.steps))
        add("A13", "a non-live executor is labelled", labelled,
            f"live=false on its ledger entries, '({kind})' in the trailer, "
            f"{kind.upper()} in the output")

    add("outcome", "the change turns the check green",
        delivery.check_outcomes.get(scenario.red_check) == "pass"
        and delivery.outcome in ("passed", "held-for-approval"),
        f"{scenario.red_check}: {delivery.check_outcomes.get(scenario.red_check)}; "
        f"Tier {delivery.tier}, gate {delivery.outcome}")

    entry = ledger.append(sign_submission(
        keydir, CONFORMANCE, "conformance.result",
        {"subject": f"executor:{executor.id}", "scenario": scenario.id,
         "executor": {"id": executor.id, "kind": kind, "version": executor.version},
         "live": kind == "live", "verdict": run.verdict,
         "clauses": [{"criterion": c.criterion, "clause": c.name, "status": c.status}
                     for c in run.clauses]},
        [scenario.change, scenario.spec]))
    run.clauses.append(Clause("record", "result recorded", "pass",
                              f"{entry.id} conformance.result by {CONFORMANCE}"))
    return run


def render(run: SuiteRun) -> str:
    head = f"conformance: executor '{run.executor}' ({run.kind}) on scenario {run.scenario}"
    if run.kind == "recorded":
        head += "\nRECORDED: no model ran. This judges the harness side of the contract only."
    elif run.kind == "scripted":
        head += ("\nSCRIPTED: a deterministic script stands in for the model. This judges the "
                 "harness side of the contract only.")
    lines = [head]
    mark = {"pass": "PASS", "fail": "FAIL", "n/a": "n/a "}
    for clause in run.clauses:
        lines.append(f"  [{mark[clause.status]}] {clause.criterion:<8} {clause.name}")
        lines.append(f"           {clause.detail}")
    lines.append(f"\n{run.verdict}")
    return "\n".join(lines)


def to_json(run: SuiteRun) -> str:
    return json.dumps(
        {"executor": run.executor, "kind": run.kind, "scenario": run.scenario,
         "verdict": run.verdict, "clauses": [c.__dict__ for c in run.clauses]}, indent=2)
