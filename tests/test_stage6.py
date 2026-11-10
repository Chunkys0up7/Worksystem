"""Stage 6 (SPEC-0004): the agent runtime.

Every release used here went through the harness: work order, gates, two
approvals, sealed manifest. Every model is SCRIPTED.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from conformance.scenario import load_scenario
from conformance.suite import open_env
from conformance.world import MODELS, UNITS, deliver_and_release, make_engine, releases_dir
from executors.models import ScriptedExplainer
from harness import chain
from harness.gateway import GatewayRefused
from harness.ledger import LedgerRejected, sign_submission
from runtime import evals
from runtime.engine import Graph, Node, NodeResult, RuntimeRefused
from runtime.income import GRAPH, guard, template
from runtime.vault import Vault, VaultMiss

REVIEWER = "case-reviewer-dee"


@pytest.fixture(scope="module")
def scenario(root):
    return load_scenario(root, "pd-0002")


@pytest.fixture(scope="module")
def cases(root):
    return {c["id"]: c for c in evals.catalogue(root)}


@pytest.fixture
def world(scenario, root, keydir, tmp_path):
    """A prepared repository with decision-income r1 released the proper way."""
    env = open_env(scenario, root, tmp_path)
    deliver_and_release(env, scenario, keydir, "recorded")
    return env


def start(engine, cases, case_id, name=None):
    return engine.start(name or case_id, cases[case_id]["documents"], UNITS, MODELS)


def facts(env, case_id):
    return [e.fact_type for e in env.ledger.entries() if case_id in e.refs]


# -------------------------------------------------------------- the happy path

def test_a_straight_case_runs_to_completion(world, keydir, cases):
    engine = make_engine(world, keydir)
    start(engine, cases, "SYN-002")
    state = engine.run("SYN-002")
    assert state["status"] == "completed" and state["decided_by"] == "decision-income@r1"
    assert state["cards_applied"] == ["CALC-0001", "FACT-0001", "RULE-0001"]
    vault = Vault(world.workdir / "vault")
    assert vault.get(state["result_ref"])["qualifying_monthly"] == cases["SYN-002"]["expect"]["qualifying"]
    assert "6500.00" in vault.get(state["explanation_ref"])
    assert facts(world, "SYN-002") == [
        "case.started", "node.completed", "node.completed", "decision.made", "node.completed",
        "agent.output", "node.completed", "case.completed"]
    writers = {e.fact_type: e.actor for e in world.ledger.entries() if "SYN-002" in e.refs}
    assert writers["decision.made"] == "svc-decision-income"
    assert writers["agent.output"] == "rt-explainer"
    assert writers["case.completed"] == "rt-orchestrator"
    assert world.ledger.verify() == []


def test_the_rule_released_through_the_harness_is_the_rule_the_runtime_applies(world, keydir, cases):
    """SYN-008 needs RULE-0002, which exists only because the change was built and released."""
    engine = make_engine(world, keydir)
    start(engine, cases, "SYN-008")
    state = engine.run("SYN-008")
    assert "RULE-0002" in state["cards_applied"]
    assert Vault(world.workdir / "vault").get(state["result_ref"])["qualifying_monthly"] == "7250.00"
    receipt = next(e for e in world.ledger.find(fact_type="decision.made") if "SYN-008" in e.refs)
    manifest = next(e for e in world.ledger.find(fact_type="release.manifest"))
    assert receipt.payload["manifest"] == manifest.id and receipt.payload["version"] == "r1"
    assert manifest.payload["change"] == "CHG-pd-0002"


# -------------------------------------------------- R1, R2: one transaction

def test_checkpoint_and_ledger_commit_together(world, keydir, cases):
    """R1: the process dies after the node ran and before commit. Nothing of that step exists."""
    crashing = make_engine(world, keydir, crash=lambda node, step: node == "calculate")
    start(crashing, cases, "SYN-001")
    with pytest.raises(RuntimeError, match="process died after node calculate"):
        crashing.run("SYN-001")
    state = crashing.state("SYN-001")
    assert state["node"] == "calculate" and "result_ref" not in state
    assert world.ledger.find(fact_type="decision.made") == []
    assert crashing.reconcile() == [] and world.ledger.verify() == []

    recovered = make_engine(world, keydir)  # a new process picks the case up
    state = recovered.run("SYN-001")
    assert state["status"] == "completed"
    assert len(world.ledger.find(fact_type="decision.made")) == 1, "decided once, not twice"
    assert recovered.reconcile() == []


def test_a_rejected_ledger_entry_rolls_back_the_checkpoint(world, keydir, cases):
    """R1, the other way: if the evidence cannot be written, the case does not move."""
    def rogue(ctx, state):
        return NodeResult({"documents_checked": 1}, next="conflict",
                          facts=[("rt-orchestrator", "case.routed", {"detail": {"name": "A. Applicant"}})])

    graph = replace(GRAPH, nodes={**GRAPH.nodes, "intake": replace(GRAPH.nodes["intake"], fn=rogue)})
    engine = make_engine(world, keydir, graph=graph)
    start(engine, cases, "SYN-001")
    with pytest.raises(LedgerRejected) as caught:
        engine.step("SYN-001")
    assert caught.value.code == "personal-data-in-payload"
    assert engine.state("SYN-001")["step"] == 0 and engine.reconcile() == []


def test_runtime_reconciles(world, keydir, cases):
    """R2."""
    engine = make_engine(world, keydir)
    for case_id in ("SYN-001", "SYN-004"):
        start(engine, cases, case_id)
        engine.run(case_id)
    assert engine.reconcile() == []
    with world.ledger.transaction() as tx:
        tx.execute("INSERT INTO runtime_checkpoints (case_id, step, node, state) VALUES (?, ?, ?, ?)",
                   ("SYN-001", 99, "ghost", "{}"))
    assert engine.reconcile() == ["SYN-001: step 99 is checkpointed with no ledger entry"]


# ------------------------------------------ R3, R4: who may decide, and say

def test_only_the_decision_service_decides(world, keydir, cases):
    """R3: an agent node writes the result itself. The case halts and nothing is decided."""
    def overreach(ctx, state):
        return NodeResult({"result_ref": "vault://x/made-up", "explanation_ref": "vault://x/e"},
                          status="completed")

    graph = replace(GRAPH, nodes={**GRAPH.nodes, "explain": replace(GRAPH.nodes["explain"], fn=overreach)})
    engine = make_engine(world, keydir, graph=graph)
    start(engine, cases, "SYN-001")
    state = engine.run("SYN-001")
    assert state["status"] == "halted"
    halted = next(e for e in world.ledger.find(fact_type="case.halted"))
    assert "wrote 'result_ref', which its contract does not declare" in halted.payload["reason"]
    assert world.ledger.find(fact_type="case.completed") == []

    declared = replace(GRAPH.nodes["explain"], fn=overreach, writes=("result_ref", "explanation_ref"))
    engine2 = make_engine(world, keydir, graph=replace(GRAPH, nodes={**GRAPH.nodes, "explain": declared}))
    start(engine2, cases, "SYN-001", "SYN-001-b")
    assert engine2.run("SYN-001-b")["status"] == "halted"
    reasons = [e.payload["reason"] for e in world.ledger.find(fact_type="case.halted")]
    assert any("wrote the regulated field 'result_ref'; only calculate may" in r for r in reasons)


def test_an_agent_node_cannot_sign_as_the_decision_service(world, keydir, cases):
    def impersonate(ctx, state):
        return NodeResult({"explanation_ref": "vault://x/e"}, status="completed",
                          facts=[("svc-decision-income", "decision.made", {"unit": "decision-income"})])

    graph = replace(GRAPH, nodes={**GRAPH.nodes, "explain": replace(GRAPH.nodes["explain"], fn=impersonate)})
    engine = make_engine(world, keydir, graph=graph)
    start(engine, cases, "SYN-001")
    assert engine.run("SYN-001")["status"] == "halted"
    assert len(world.ledger.find(fact_type="decision.made")) == 1
    assert "tried to act as svc-decision-income" in world.ledger.find(fact_type="case.halted")[0].payload["reason"]
    with pytest.raises(LedgerRejected):
        world.ledger.append(sign_submission(keydir, "rt-explainer", "decision.made", {"subject": "x"}))


RESULT = {"qualifying_monthly": "6500.00", "base_monthly": "6000.00", "variable_monthly": "500.00",
          "cards_applied": ["CALC-0001", "FACT-0001", "RULE-0001"]}


@pytest.mark.parametrize("text, problem", [
    (template(RESULT), None),
    ("Your income is 6500.00 (6000.00 plus 500.00) under CALC-0001, FACT-0001, RULE-0001.", None),
    ("Your income is 9999.99 under CALC-0001, FACT-0001, RULE-0001.", "did not return"),
    ("Your income is 6,500.00 or perhaps 6,600.00. CALC-0001 FACT-0001 RULE-0001", "did not return"),
    ("Base 6000.00 plus 500.00 under CALC-0001, FACT-0001, RULE-0001.", "does not state the qualifying figure"),
    ("Your income is 6500.00.", "does not name CALC-0001"),
])
def test_explanation_guard_rules(text, problem):
    found = guard(text, RESULT)
    assert (problem in found) if problem else found is None


@pytest.mark.parametrize("adapter, used", [
    (ScriptedExplainer(), "model draft"),
    (ScriptedExplainer(invent=True), "template"),
    (ScriptedExplainer(omit_cards=True), "template"),
])
def test_explanation_guard(world, keydir, cases, adapter, used):
    """R4: a draft that states another amount is discarded; the applicant gets the template."""
    engine = make_engine(world, keydir, adapters={"scripted-explainer": adapter})
    start(engine, cases, "SYN-002")
    state = engine.run("SYN-002")
    assert state["status"] == "completed"
    output = next(e for e in world.ledger.find(fact_type="agent.output"))
    assert output.payload["used"] == used and output.payload["model_kind"] == "scripted"
    text = Vault(world.workdir / "vault").get(state["explanation_ref"])
    assert "6500.00" in text and "9999.99" not in text
    assert len(world.ledger.find(fact_type="guard.tripped")) == (0 if used == "model draft" else 1)


# ---------------------------------------------------------------- R5: pinning

@pytest.fixture
def two_versions(world, scenario, keydir):
    """r2 is released while cases are running. It is a different implementation."""
    engine = make_engine(world, keydir)
    return world, engine


def test_pinning_a_running_case_keeps_its_version(two_versions, scenario, keydir, cases):
    """R5."""
    world, engine = two_versions
    start(engine, cases, "SYN-004")
    assert engine.run("SYN-004")["status"] == "waiting-human"

    second = deliver_and_release(world, scenario, keydir, "loop-b", change_id="CHG-pd-0002-b", tag="w2")
    assert second.version == "r2"
    assert json.loads((releases_dir(world) / "decision-income/channel.json").read_text())["current"] == "r2"

    start(engine, cases, "SYN-001")
    assert engine.run("SYN-001")["decided_by"] == "decision-income@r2", "a new case starts on the head"

    engine.resume("SYN-004", REVIEWER, {**cases["SYN-004"]["documents"][0]})
    state = engine.run("SYN-004")
    assert state["status"] == "completed"
    assert state["decided_by"] == "decision-income@r1", "the running case kept the version it started with"
    receipts = {e.payload["subject"]: e.payload["version"] for e in world.ledger.find(fact_type="decision.made")}
    assert receipts == {"SYN-001": "r2", "SYN-004": "r1"}


def test_pinning_migration_needs_an_approval_and_is_recorded(two_versions, scenario, keydir, cases):
    """R5."""
    world, engine = two_versions
    start(engine, cases, "SYN-004")
    engine.run("SYN-004")
    deliver_and_release(world, scenario, keydir, "loop-b", change_id="CHG-pd-0002-b", tag="w2")

    with pytest.raises(RuntimeRefused, match="no approved migration"):
        engine.migrate("SYN-004", "decision-income", "r2")
    world.ledger.append(sign_submission(
        keydir, "approver-ben", "migration.approved",
        {"subject": "SYN-004", "unit": "decision-income", "to_version": "r2"}, ["SYN-004"]))
    with pytest.raises(RuntimeRefused, match="not a stored release"):
        world.ledger.append(sign_submission(
            keydir, "approver-ben", "migration.approved",
            {"subject": "SYN-004", "unit": "decision-income", "to_version": "r7"}, ["SYN-004"]))
        engine.migrate("SYN-004", "decision-income", "r7")
    engine.migrate("SYN-004", "decision-income", "r2")
    migrated = world.ledger.find(fact_type="case.migrated")[0].payload
    assert (migrated["from_version"], migrated["to_version"]) == ("r1", "r2")
    engine.resume("SYN-004", REVIEWER, {**cases["SYN-004"]["documents"][0]})
    assert engine.run("SYN-004")["decided_by"] == "decision-income@r2"
    assert engine.reconcile() == []
    with pytest.raises(RuntimeRefused, match="is completed"):
        engine.migrate("SYN-004", "decision-income", "r1")
    with pytest.raises(LedgerRejected):
        world.ledger.append(sign_submission(keydir, "rt-orchestrator", "migration.approved",
                                            {"subject": "SYN-004"}))


def test_a_case_cannot_start_before_anything_is_released(scenario, root, keydir, tmp_path, cases):
    env = open_env(scenario, root, tmp_path)
    engine = make_engine(env, keydir)
    with pytest.raises(RuntimeRefused, match="has never been released"):
        start(engine, cases, "SYN-001")


# --------------------------------------------------------------------- R6

def test_tampered_release_is_not_run(world, keydir, cases):
    """R6: the stored release is edited in place. The runtime will not call it."""
    stored = releases_dir(world) / "decision-income/r1/services/decision_income/calc.py"
    stored.write_text(stored.read_text().replace("VARIABLE_HISTORY_MIN_MONTHS = 24",
                                                 "VARIABLE_HISTORY_MIN_MONTHS = 1"))
    engine = make_engine(world, keydir)
    start(engine, cases, "SYN-003")
    state = engine.run("SYN-003")
    assert state["status"] == "halted" and "result_ref" not in state
    assert "does not match its sealed manifest" in world.ledger.find(fact_type="case.halted")[0].payload["reason"]
    assert world.ledger.find(fact_type="decision.made") == []


# ----------------------------------------------------------------- R7, R8

@pytest.mark.parametrize("case_id, fields", [("SYN-004", ["base_per_period"]),
                                             ("SYN-005", ["frequency"]),
                                             ("SYN-006", ["base_per_period"])])
def test_proc_0001_conflicts_route_to_a_person(world, keydir, cases, case_id, fields):
    """R7. SYN-006 carries an instruction in a document. It changes nothing."""
    engine = make_engine(world, keydir)
    start(engine, cases, case_id)
    state = engine.run(case_id)
    assert state["status"] == "waiting-human" and state["conflict_fields"] == fields
    assert "result_ref" not in state
    routed = next(e for e in world.ledger.find(fact_type="case.routed") if case_id in e.refs)
    assert routed.payload["card"] == "PROC-0001" and routed.payload["to"] == "human"
    assert not [e for e in world.ledger.find(fact_type="decision.made") if case_id in e.refs]


def test_proc_0001_agreeing_documents_go_straight(world, keydir, cases):
    engine = make_engine(world, keydir)
    start(engine, cases, "SYN-007")
    assert engine.run("SYN-007")["status"] == "completed"
    assert world.ledger.find(fact_type="case.routed") == []


def test_human_review_is_durable(world, keydir, cases):
    """R8: route, lose the process, resume from a new engine."""
    start(make_engine(world, keydir), cases, "SYN-004")
    assert make_engine(world, keydir).run("SYN-004")["status"] == "waiting-human"

    later = make_engine(world, keydir)
    assert later.run("SYN-004")["status"] == "waiting-human", "running again does not move it"
    with pytest.raises(LedgerRejected) as caught:
        later.resume("SYN-004", "rt-orchestrator", {"base_per_period": "6500.00"})
    assert caught.value.code == "not-authoritative-writer"
    assert later.state("SYN-004")["status"] == "waiting-human"

    chosen = cases["SYN-004"]["documents"][0]
    later.resume("SYN-004", REVIEWER, chosen)
    state = later.run("SYN-004")
    assert state["status"] == "completed"
    assert Vault(world.workdir / "vault").get(state["result_ref"])["qualifying_monthly"] == "6000.00"
    resolved = world.ledger.find(fact_type="review.resolved")[0]
    assert resolved.actor == REVIEWER
    with pytest.raises(RuntimeRefused, match="not waiting for a person"):
        later.resume("SYN-004", REVIEWER, chosen)


# ------------------------------------------------------------------- R9

def test_erasure_leaves_the_record_and_removes_the_person(world, keydir, cases):
    """R9."""
    engine = make_engine(world, keydir)
    start(engine, cases, "SYN-002")
    state = engine.run("SYN-002")
    vault = Vault(world.workdir / "vault")
    everything = json.dumps([e.payload for e in world.ledger.entries()]) + json.dumps(
        world.ledger.query("SELECT state FROM runtime_checkpoints"))
    for value in ("6000.00", "12000.00", "6500.00", "500.00"):
        assert value not in everything, f"{value} is in the ledger or a checkpoint"
    assert "vault://SYN-002/result" in everything

    assert vault.erase("SYN-002") == 4
    with pytest.raises(VaultMiss):
        vault.get(state["result_ref"])
    assert world.ledger.verify() == [] and engine.reconcile() == []
    receipt = next(e for e in world.ledger.find(fact_type="decision.made"))
    assert receipt.payload["cards_applied"] == ["CALC-0001", "FACT-0001", "RULE-0001"]
    assert receipt.payload["version"] == "r1"


@pytest.mark.parametrize("ref", ["file:///etc/passwd", "vault://../x/y", "vault://case", "vault://a/b/c"])
def test_vault_refuses_anything_that_is_not_a_reference(tmp_path, ref):
    with pytest.raises(VaultMiss):
        Vault(tmp_path).get(ref)


# ------------------------------------------------------------------ R10

def test_data_path_register(world, keydir, cases):
    """R10: only a model approved for consumer data may be the explainer."""
    from conformance.suite import new_gateway

    gateway = new_gateway(world)
    for model in ("scripted-author-a", "scripted-reviewer"):
        with pytest.raises(GatewayRefused):
            gateway.session(model, "explainer", data_classes=("consumer-personal",))
    stripped = replace(world.policy, models={
        **world.policy.models, "scripted-explainer": {
            **world.policy.models["scripted-explainer"], "data_classes": []}})
    with pytest.raises(GatewayRefused) as caught:
        new_gateway(replace(world, policy=stripped)).session(
            "scripted-explainer", "explainer", data_classes=("consumer-personal",))
    assert caught.value.code == "model-not-approved-for-data"

    engine = make_engine(replace(world, policy=stripped), keydir)
    start(engine, cases, "SYN-001")
    state = engine.run("SYN-001")
    assert state["status"] == "completed" and state["explained_by"] == "template"
    assert "no approved data path" in world.ledger.find(fact_type="guard.tripped")[0].payload["reason"]


# ------------------------------------------------------------------ R11

def test_eval_closes_the_procedure_link(world, keydir, root):
    """R11."""
    before = chain.forward("OBL-0001", world.knowledge, world.policy, world.ledger)
    assert any("EVL-income-calculation-001 has no result" in o for o in before["open"])
    entry = evals.run_proc_0001(lambda: make_engine(world, keydir), evals.catalogue(root),
                                ledger=world.ledger, keydir=keydir, models=MODELS)
    assert entry.actor == "ci-evals"
    assert entry.payload == {"subject": "EVL-income-calculation-001", "covers": ["PROC-0001"],
                             "cases": 8, "adversarial_twins": 1, "failed": [], "outcome": "pass"}
    after = chain.forward("OBL-0001", world.knowledge, world.policy, world.ledger)
    assert not any("EVL-" in o for o in after["open"])
    assert "EVL-income-calculation-001 (eval) covers PROC-0001: PASS" in chain.render_forward(after)


def test_the_eval_fails_when_the_procedure_is_broken(world, keydir, root):
    def careless(ctx, state):
        return NodeResult({"conflict": False, "conflict_fields": []}, next="calculate")

    graph = replace(GRAPH, nodes={**GRAPH.nodes, "conflict": replace(GRAPH.nodes["conflict"], fn=careless)})
    entry = evals.run_proc_0001(lambda: make_engine(world, keydir, graph=graph), evals.catalogue(root),
                                ledger=world.ledger, keydir=keydir, models=MODELS)
    assert entry.payload["outcome"] == "fail"
    assert entry.payload["failed"] == ["SYN-004", "SYN-005", "SYN-006"]


def test_a_checkpoint_edited_behind_the_engine_is_not_trusted(world, keydir, cases):
    """Skip the person by editing the stored state. The engine checks it against the ledger."""
    engine = make_engine(world, keydir)
    start(engine, cases, "SYN-004")
    waiting = engine.run("SYN-004")
    assert waiting["status"] == "waiting-human"
    forged = {**waiting, "status": "running", "node": "calculate", "conflict": False}
    with world.ledger.transaction() as tx:
        tx.execute("UPDATE runtime_checkpoints SET state = ? WHERE case_id = ? AND step = ?",
                   (json.dumps(forged, sort_keys=True), "SYN-004", waiting["step"]))
    with pytest.raises(RuntimeRefused, match="does not match the ledger"):
        engine.run("SYN-004")
    assert not [e for e in world.ledger.find(fact_type="decision.made") if "SYN-004" in e.refs]


def test_the_pinned_release_runs_even_when_main_has_other_code(world, keydir, cases, root):
    """The stored release has no services/__init__.py; main does. The runtime must still run
    the release. SYN-008 needs RULE-0002, which main does not have."""
    assert "variable_total_recent" not in (root / "services/decision_income/calc.py").read_text()
    assert not (releases_dir(world) / "decision-income/r1/services/__init__.py").exists()
    engine = make_engine(world, keydir)
    start(engine, cases, "SYN-008")
    assert "RULE-0002" in engine.run("SYN-008")["cards_applied"]


def test_checks_fail_if_the_governed_code_came_from_somewhere_else(world):
    """A code root whose `services` has no __init__.py: Python would import main's instead."""
    import shutil

    from harness.checks import run_skill_checks

    partial = world.workdir / "partial"
    shutil.copytree(world.root / "services/decision_income", partial / "services/decision_income")
    results = run_skill_checks(world.knowledge.skills["SKL-income-calculation"], world.knowledge,
                               world.policy, code_root=partial)
    assert results and not any(r.passed for r in results)
    assert "not from the code under test" in results[0].detail


def test_calculate_refuses_a_conflicting_case_even_if_routing_sends_it(world, keydir, cases):
    """Defence in depth: a routing bug sends a conflicting case straight to calculate."""
    from runtime.income import conflict as real_conflict

    def misroutes(ctx, state):
        result = real_conflict(ctx, state)
        return NodeResult(result.updates, next="calculate")

    graph = replace(GRAPH, nodes={**GRAPH.nodes, "conflict": replace(GRAPH.nodes["conflict"], fn=misroutes)})
    engine = make_engine(world, keydir, graph=graph)
    start(engine, cases, "SYN-004")
    state = engine.run("SYN-004")
    assert state["status"] == "halted" and "result_ref" not in state
    assert "PROC-0001" in world.ledger.find(fact_type="case.halted")[0].payload["reason"]
    assert world.ledger.find(fact_type="decision.made") == []
