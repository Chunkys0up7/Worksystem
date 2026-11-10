"""Stage 7 (SPEC-0005): the defect and change loop.

The defect is real: the synthetic decision service fails on an amount that is
not a number and, in one path, accepts an infinite one. Every model is SCRIPTED.
"""

from __future__ import annotations

import pytest

from conformance.scenario import load_scenario
from conformance.suite import open_env
from conformance.world import (
    MODELS, UNITS, advance, deliver_and_release, make_engine, releases_dir,
)
from executors import load as load_executor
from harness import defect
from harness.delivery import run_work_order
from harness.knowledge import load_knowledge, validate
from harness.ledger import LedgerRejected, sign_submission
from harness.workorder import build_work_order
from runtime import evals
from runtime.vault import Vault

SKILL = "SKL-income-calculation"
REGRESSION = "CHK-income-calculation-006"


@pytest.fixture(scope="module")
def cases(root):
    return {c["id"]: c for c in evals.catalogue(root)}


@pytest.fixture
def production(root, keydir, tmp_path, cases):
    """r1 is released and two cases with non-finite amounts have been through it."""
    env = open_env(load_scenario(root, "pd-0002"), root, tmp_path)
    deliver_and_release(env, load_scenario(root, "pd-0002"), keydir, "recorded")
    engine = make_engine(env, keydir)
    states = {}
    for case_id in ("SYN-009", "SYN-010"):
        engine.start(case_id, cases[case_id]["documents"], UNITS, MODELS)
        states[case_id] = engine.run(case_id)
    return env, engine, states


@pytest.fixture
def triaged(production, root, keydir):
    """The defect is open, and main has moved on to hold the regression check."""
    env, engine, states = production
    trace = env.ledger.find(fact_type="case.halted")[0]
    opened = defect.open_defect("DEF-0001", "The income service fails on a non-finite amount",
                                traces=[trace.id], unit="decision-income", version="r1",
                                ledger=env.ledger, keydir=keydir)
    scenario = load_scenario(root, "defect-0001")
    later = advance(env, scenario, root, "main2")
    return env, later, scenario, engine, opened


def reproduce(later, keydir):
    return defect.reproduce_against_release(
        "DEF-0001", later.knowledge.skills[SKILL], knowledge=later.knowledge, policy=later.policy,
        releases_dir=releases_dir(later), unit="decision-income", version="r1",
        ledger=later.ledger, keydir=keydir)


def test_the_defect_is_real_in_the_released_version(production):
    """One input crashes the service. Another is accepted and a figure is produced."""
    env, engine, states = production
    assert states["SYN-009"]["status"] == "halted"
    reason = env.ledger.find(fact_type="case.halted")[0].payload["reason"]
    assert "decision-income@r1 failed" in reason and "InvalidOperation" in reason
    # The worse fault: an infinite amount went through and the case completed.
    assert states["SYN-010"]["status"] == "completed"
    result = Vault(env.workdir / "vault").get(states["SYN-010"]["result_ref"])
    assert result["qualifying_monthly"] == "5000.00"


def test_defect_cites_the_ledger(production, keydir):
    """D1."""
    env, _, _ = production
    with pytest.raises(defect.DefectError, match="cites the ledger entries that show it"):
        defect.open_defect("DEF-0001", "x", traces=[], unit="u", version="r1",
                           ledger=env.ledger, keydir=keydir)
    with pytest.raises(defect.DefectError, match="not in the ledger: LED-9999"):
        defect.open_defect("DEF-0001", "x", traces=["LED-9999"], unit="u", version="r1",
                           ledger=env.ledger, keydir=keydir)
    with pytest.raises(defect.DefectError, match="not a valid defect id"):
        defect.open_defect("bug-1", "x", traces=["LED-1"], unit="u", version="r1",
                           ledger=env.ledger, keydir=keydir)
    trace = env.ledger.find(fact_type="case.halted")[0]
    entry = defect.open_defect("DEF-0001", "fails on a non-finite amount", traces=[trace.id],
                               unit="decision-income", version="r1", ledger=env.ledger,
                               keydir=keydir)
    assert entry.actor == "ci-triage" and entry.payload["traces"] == [trace.id]
    assert "NaN" not in str(entry.payload)
    with pytest.raises(LedgerRejected):
        env.ledger.append(sign_submission(keydir, "executor-loop", "defect.opened",
                                          {"subject": "DEF-0002"}))


def test_reproduction_against_the_release(triaged, keydir):
    """D2: the new regression check and held-out cases fail on r1, as they must."""
    env, later, scenario, _, _ = triaged
    entry = reproduce(later, keydir)
    assert entry.actor == "ci-gates" and entry.payload["reproduced"] is True
    assert entry.payload["against"] == "decision-income@r1"
    assert entry.payload["failing_checks"] == [REGRESSION]
    assert entry.payload["heldout_failures"] == 6  # minus infinity was already refused as negative
    assert defect.not_reproduced(["DEF-0001"], env.ledger) == []
    assert defect.not_reproduced(["DEF-0002"], env.ledger) == ["DEF-0002"]


def test_a_defect_that_does_not_reproduce_is_recorded_as_such(production, root, keydir):
    """Run today's checks against r1: they pass. That is not a reproduction."""
    env, _, _ = production
    trace = env.ledger.find(fact_type="case.halted")[0]
    defect.open_defect("DEF-0001", "x", traces=[trace.id], unit="decision-income", version="r1",
                       ledger=env.ledger, keydir=keydir)
    entry = defect.reproduce_against_release(
        "DEF-0001", env.knowledge.skills[SKILL], knowledge=env.knowledge, policy=env.policy,
        releases_dir=releases_dir(env), unit="decision-income", version="r1",
        ledger=env.ledger, keydir=keydir)
    assert entry.payload["reproduced"] is False
    assert defect.not_reproduced(["DEF-0001"], env.ledger) == ["DEF-0001"]
    with pytest.raises(defect.DefectError, match="has not been opened"):
        defect.reproduce("DEF-0404", env.knowledge.skills[SKILL], knowledge=env.knowledge,
                         policy=env.policy, code_root=env.root, against="x",
                         ledger=env.ledger, keydir=keydir)


def test_no_fix_before_reproduction(triaged, keydir):
    """D3: the spec is approved and the executor is ready. Without a reproduction, no work."""
    env, later, scenario, _, _ = triaged
    assert later.knowledge.specs["SPEC-0010"].fixes == ["DEF-0001"]
    executor = load_executor("recorded", later.scenario_dir, later.policy)

    def attempt(tag):
        order = build_work_order(scenario.change, scenario.spec, executor=executor.identity,
                                 root=later.root, policy=later.policy, knowledge=later.knowledge,
                                 workdir=later.workdir / tag)
        return order, run_work_order(order, executor, ledger=later.ledger, keydir=keydir,
                                     policy=later.policy, knowledge=later.knowledge)

    before = len(later.ledger.entries())
    order, run = attempt("try1")
    assert run.outcome == "blocked"
    assert "has not been reproduced. Reproduce before fixing." in run.steps[0]
    assert len(later.ledger.entries()) == before, "nothing was proposed"
    assert order.manifest == __import__("harness.workorder", fromlist=["snapshot"]).snapshot(order.worktree)

    reproduce(later, keydir)
    _, run = attempt("try2")
    assert run.outcome == "held-for-approval"
    gate = later.ledger.find(fact_type="gate.result")[-1]
    assert "DEF-0001" in gate.refs


def test_a_spec_must_name_a_real_defect_id(triaged):
    _, later, _, _, _ = triaged
    path = later.root / "specs/SPEC-0010-refuse-non-finite-amounts.md"
    path.write_text(path.read_text().replace("fixes: [DEF-0001]", "fixes: [bug-17]"))
    assert any("fixes 'bug-17', which is not a defect id" in p
               for p in validate(load_knowledge(later.root), later.policy))


def test_fix_takes_the_usual_path(triaged, keydir):
    """D4."""
    env, later, scenario, _, _ = triaged
    reproduce(later, keydir)
    from harness.tower import make_tower

    fix = deliver_and_release(later, scenario, keydir, "loop", tag="fix",
                              tower=make_tower(later.policy, later.knowledge))
    assert (fix.unit, fix.version) == ("decision-income", "r2")
    manifest = fix.manifest
    assert manifest["change"] == "CHG-def-0001" and manifest["spec"] == "SPEC-0010"
    assert manifest["tier"] == "C" and len(manifest["approvals"]) == 2
    kinds = [e.fact_type for e in later.ledger.entries() if "CHG-def-0001" in e.refs]
    for needed in ("change.proposed", "handback.result", "tier.assigned", "check.result",
                   "tower.result", "gate.result", "approval.recorded", "release.approved",
                   "release.manifest"):
        assert needed in kinds, needed
    assert kinds.count("tower.result") == 3 and kinds.count("check.result") == 6
    assert later.ledger.verify() == [] and later.ledger.reconcile() == []


def test_regression_check_is_in_the_skill(triaged, keydir):
    """D5: the fix was built against skill 1.1.1, which carries the regression check for good."""
    env, later, scenario, _, _ = triaged
    reproduce(later, keydir)
    fix = deliver_and_release(later, scenario, keydir, "recorded", tag="fix")
    assert fix.manifest["skills"] == [{"id": SKILL, "version": "1.1.1",
                                       "content_hash": fix.manifest["skills"][0]["content_hash"]}]
    skill = later.knowledge.skills[SKILL]
    assert REGRESSION in [c["id"] for c in skill.checks]
    passed = {e.payload["subject"]: e.payload["outcome"]
              for e in later.ledger.find(fact_type="check.result") if "CHG-def-0001" in e.refs}
    assert passed[REGRESSION] == "pass"


def test_fix_reaches_new_cases_only(triaged, keydir, cases):
    """D6."""
    env, later, scenario, engine, _ = triaged
    engine.start("held-open", cases["SYN-004"]["documents"], UNITS, MODELS)
    assert engine.run("held-open")["status"] == "waiting-human"

    reproduce(later, keydir)
    deliver_and_release(later, scenario, keydir, "recorded", tag="fix")

    # A new case with the infinite amount: r1 accepted it. r2 refuses it.
    engine.start("after-fix", cases["SYN-010"]["documents"], UNITS, MODELS)
    after = engine.run("after-fix")
    assert after["status"] == "halted" and "result_ref" not in after
    reasons = [e.payload["reason"] for e in env.ledger.find(fact_type="case.halted")]
    assert any("the decision service refused the inputs" in r for r in reasons)
    assert engine.pins("after-fix")["decision-income"] == "r2"

    engine.resume("held-open", "case-reviewer-dee", cases["SYN-004"]["documents"][0])
    assert engine.run("held-open")["decided_by"] == "decision-income@r1"


def test_return_path_needs_the_owner(triaged):
    """D7: a card drafted from traces is not knowledge until its owner signs it."""
    _, later, _, _, _ = triaged
    card = later.root / "knowledge/cards/RULE-0002.yaml"
    original = card.read_text()
    card.write_text(original + "provenance: drafted-from-traces\ntraces: [LED-12]\n")
    problems = validate(load_knowledge(later.root), later.policy)
    assert any("RULE-0002: drafted from traces and not signed off by its owner" in p for p in problems)
    card.write_text(original + "provenance: drafted-from-traces\ntraces: [LED-12]\napproved_by: someone-else\n")
    assert any("not signed off by its owner (income-policy-sme)" in p
               for p in validate(load_knowledge(later.root), later.policy))
    card.write_text(original + "provenance: drafted-from-traces\napproved_by: income-policy-sme\n")
    assert any("drafted from traces but cites none" in p
               for p in validate(load_knowledge(later.root), later.policy))
    card.write_text(original + "provenance: drafted-from-traces\ntraces: [LED-12]\napproved_by: income-policy-sme\n")
    assert validate(load_knowledge(later.root), later.policy) == []


def test_materialise_makes_a_release_importable(production, tmp_path):
    env, _, _ = production
    root = defect.materialise(releases_dir(env) / "decision-income/r1", tmp_path / "m")
    assert (root / "services/__init__.py").exists()
    assert (root / "services/decision_income/calc.py").exists()
    assert not (root / "manifest.json").exists()
