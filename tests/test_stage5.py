"""Stage 5 (SPEC-0003): the confidence tower, two-person release, degraded mode.

Test names follow the acceptance criteria in the spec where one applies.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from attacks import executors as doubles
from conformance.scenario import load_scenario
from conformance.suite import deliver, new_gateway, open_env
from executors.recorded import RecordedExecutor
from harness import degraded
from harness.checks import run_heldout
from harness.ledger import LedgerRejected, sign_submission
from harness.release import ReleaseRefused, approve, release, verify_stored
from harness.tower import _heldout, fitness, make_tower, mutation
from harness.workorder import build_work_order

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
CALC = "services/decision_income/calc.py"


@pytest.fixture(scope="module")
def scenario(root):
    return load_scenario(root, "pd-0002")


@pytest.fixture
def env(scenario, root, tmp_path):
    return open_env(scenario, root, tmp_path)


def go(env, scenario, keydir, cls=RecordedExecutor, *, tower=True, launched_by="approver-ana"):
    executor = cls(doubles.recording(scenario.dir))
    order, run = deliver(env, scenario, executor, keydir, gateway=new_gateway(env),
                         launched_by=launched_by,
                         tower=make_tower(env.policy, env.knowledge) if tower else None)
    return order, run


def facts(env):
    return [e.fact_type for e in env.ledger.entries()]


# ------------------------------------------------------------------ the tower

def test_tower_gates_by_tier(env, scenario, keydir):
    """B1: each tier has its gates, and each gate records a number and a threshold."""
    assert env.policy.tower["tiers"] == {"A": ["fitness"], "B": ["fitness"],
                                         "C": ["fitness", "heldout", "mutation"]}
    order, run = go(env, scenario, keydir)
    assert run.outcome == "held-for-approval"
    recorded = {e.payload["gate"]: e.payload for e in env.ledger.find(fact_type="tower.result")}
    assert list(recorded) == ["fitness", "heldout", "mutation"]
    for payload in recorded.values():
        assert payload["number"] is not None and payload["passed"] is True
    assert recorded["fitness"]["number"] == 0 and recorded["heldout"]["number"] == 0
    assert recorded["mutation"]["number"] >= recorded["mutation"]["threshold"] == 0.8
    assert "mutants killed" in recorded["mutation"]["detail"]
    assert all(e.actor == "ci-gates" for e in env.ledger.find(fact_type="tower.result"))
    gate = env.ledger.find(fact_type="gate.result")[0].payload
    assert [t["gate"] for t in gate["tower"]] == ["fitness", "heldout", "mutation"]

    tower = make_tower(env.policy, env.knowledge)
    assert [r.gate for r in tower(order, "A", [CALC])] == ["fitness"]
    assert [r.gate for r in tower(order, "B", [CALC])] == ["fitness"]


def test_no_number_no_merge(env, scenario):
    """B2: Tier C with no held-out cases has no number, and that blocks."""
    skill = env.knowledge.skills["SKL-income-calculation"]
    import shutil

    shutil.rmtree(env.root / "heldout")
    result = _heldout(env.root, [skill], env.knowledge)
    assert result.number is None and result.passed is False
    assert "no held-out cases for SKL-income-calculation" in result.detail
    assert _heldout(env.root, [], env.knowledge).passed is False

    unknown = replace(env.policy, tower={**env.policy.tower, "tiers": {"C": ["made-up-gate"]}})
    order = build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                             root=env.root, policy=env.policy, knowledge=env.knowledge,
                             workdir=env.workdir)
    [only] = make_tower(unknown, env.knowledge)(order, "C", [CALC])
    assert only.number is None and only.passed is False


def test_a_failed_gate_stops_the_tower_and_blocks(env, scenario, keydir):
    _, run = go(env, scenario, keydir, doubles.UsesFloat)
    assert run.outcome == "blocked"
    assert [t.gate for t in run.tower] == ["fitness"], "later gates are not run"
    assert run.tower[0].number == 3 and not run.tower[0].passed
    assert env.ledger.find(fact_type="gate.result")[0].payload["tower"][0]["passed"] is False


# ------------------------------------------------------------------- fitness

RULES = {r["id"]: r for r in __import__("yaml").safe_load(
    (Path(__file__).resolve().parent.parent / "policy/fitness.yaml").read_text())["rules"]}


@pytest.mark.parametrize("rule, rel, source, expect", [
    ("FIT-001", "services/x/calc.py", "import os\n", "imports os"),
    ("FIT-001", "services/x/calc.py", "from decimal import Decimal\nfrom . import helper\n", None),
    ("FIT-001", "services/x/calc.py", "def f():\n    import json\n", "imports json"),
    ("FIT-001", "services/x/calc.py", "from services.y import z\n", None),
    ("FIT-002", "services/x/calc.py", "x = 1.5\n", "floating-point literal"),
    ("FIT-002", "services/x/calc.py", "x = float('1')\n", "float()"),
    ("FIT-002", "services/x/calc.py", "from decimal import Decimal\nx = Decimal('1.5')\n", None),
    ("FIT-003", "harness/thing.py", "import executors\n", "imports executors"),
    ("FIT-003", "harness/thing.py", "from executors.loop import LoopExecutor\n", "imports executors.loop"),
    ("FIT-003", "harness/thing.py", "def f():\n    from conformance import suite\n", "imports conformance"),
    ("FIT-003", "harness/thing.py", "from .ledger import Ledger\n", None),
    ("FIT-004", "executors/x.py", "from harness.ledger import Ledger\n", "imports harness.ledger"),
    ("FIT-004", "executors/x.py", "from harness import identity\n", "imports harness.identity"),
    ("FIT-004", "executors/x.py", "from harness.workorder import HandBack\n", None),
    ("FIT-005", "runtime/x.py", "import sqlite3\n", "imports sqlite3"),
    ("FIT-001", "services/x/calc.py", "def broken(:\n", "does not parse"),
])
def test_fitness_rules(rule, rel, source, expect):
    """B3."""
    found = fitness.check_file(rel, source, RULES[rule])
    assert (any(expect in f for f in found) if expect else found == []), found


def test_fitness_holds_on_this_repository(root, policy):
    assert fitness.run(root, policy.fitness_rules) == []


def test_fitness_exempts_only_the_edge(root, policy):
    """The command line is the one place adapters are named. Nowhere else in the harness."""
    rule = RULES["FIT-003"]
    assert rule["except"] == ["harness/cli.py"]
    strict = [{**rule, "except": []}]
    found = fitness.run(root, strict)
    assert found and all(f.startswith("harness/cli.py:") for f in found)


# ------------------------------------------------------------------- held out

def test_heldout_cases_never_reach_a_worktree(env, scenario):
    """B4."""
    order = build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                             root=env.root, policy=env.policy, knowledge=env.knowledge,
                             workdir=env.workdir)
    assert (env.root / "heldout/income-calculation/vectors.json").exists()
    leaked = [p for p in order.manifest
              if p.startswith(("heldout/", "conformance/", "demo-keys/"))]
    assert leaked == []
    assert order.tools.call("read_file", {"path": "heldout/income-calculation/vectors.json"})["ok"] is False
    assert order.tools.call("list_files", {"prefix": "heldout"})["files"] == []


def test_heldout_blocks_a_change_that_fits_only_the_visible_checks(env, scenario, keydir):
    """B4: the visible check passes. The cases the executor never saw do not."""
    _, run = go(env, scenario, keydir, doubles.Overfits)
    assert set(run.check_outcomes.values()) == {"pass"}, "every paired check passes"
    assert run.outcome == "blocked"
    held = next(t for t in run.tower if t.gate == "heldout")
    assert held.number > 0 and "wrong result" in held.detail
    assert [t.gate for t in run.tower] == ["fitness", "heldout"]


def test_heldout_runner_reports_cases_and_failures(env):
    skill = env.knowledge.skills["SKL-income-calculation"]
    cases, failures = run_heldout(skill, env.knowledge, env.root)
    # The overlaid repository has the new rule's cases and the old code: they fail.
    assert cases == 21 and failures
    (env.root / "heldout/income-calculation/run.py").write_text("raise RuntimeError('broken')\n")
    assert run_heldout(skill, env.knowledge, env.root)[1][0].startswith("held-out runner failed")


# -------------------------------------------------------------------- mutation

def test_mutation_operators():
    source = "def f(a, b):\n    if a >= 24 and b < 0:\n        return a + b * 2\n    return a\n"
    found = {m.description for m in mutation.mutants(source)}
    assert found == {">= became >", "< became <=", "and became or", "+ became -", "* became /",
                     "24 became 25", "0 became 1", "2 became 3"}
    for mutant in mutation.mutants(source):
        compile(mutant.source, "<mutant>", "exec")
        assert mutant.source != source


def test_mutation_leaves_docstrings_and_strings_alone():
    source = 'def f():\n    """Uses 24 months."""\n    return "24" == "24"\n'
    assert [m.description for m in mutation.mutants(source)] == ["== became !="]


def test_mutation_blocks_code_the_checks_do_not_exercise(env, scenario, keydir):
    """B5: a correct change with a branch nothing tests. Checks and held-out cases pass."""
    _, run = go(env, scenario, keydir, doubles.UntestedBranch)
    assert set(run.check_outcomes.values()) == {"pass"}
    gates = {t.gate: t for t in run.tower}
    assert gates["fitness"].passed and gates["heldout"].passed
    assert gates["mutation"].passed is False and gates["mutation"].number < 0.8
    assert "survivors" in gates["mutation"].detail
    assert run.outcome == "blocked"


# -------------------------------------------------------------------- release

@pytest.fixture
def held(env, scenario, keydir):
    """A Tier C change that passed its gates and is held for approval. No tower: fast."""
    order, run = go(env, scenario, keydir, tower=False)
    assert run.outcome == "held-for-approval"
    return order


def do_release(env, order, keydir, tmp_path):
    return release(order.change_id, ledger=env.ledger, keydir=keydir, policy=env.policy,
                   code_root=order.worktree, releases_dir=tmp_path / "releases")


def test_release_needs_two_approvers(env, held, keydir, tmp_path):
    """B6."""
    with pytest.raises(ReleaseRefused, match="0 of 2 approvals"):
        do_release(env, held, keydir, tmp_path)
    approve(env.ledger, keydir, "approver-ben", held.change_id)
    with pytest.raises(ReleaseRefused, match="1 of 2 approvals"):
        do_release(env, held, keydir, tmp_path)
    approve(env.ledger, keydir, "approver-ben", held.change_id)
    with pytest.raises(ReleaseRefused, match="1 of 2 approvals"):
        do_release(env, held, keydir, tmp_path)  # one person counts once
    approve(env.ledger, keydir, "approver-cy", held.change_id)
    done = do_release(env, held, keydir, tmp_path)
    assert (done.unit, done.version) == ("decision-income", "r1")
    assert env.ledger.find(fact_type="release.approved")[0].payload["approvers"] == [
        "approver-ben", "approver-cy"]
    assert facts(env).count("release.refused") == 3
    assert env.ledger.verify() == []


def test_release_launcher_cannot_approve(env, held, keydir, tmp_path):
    """B6: approver-ana launched this work."""
    approve(env.ledger, keydir, "approver-ana", held.change_id)
    approve(env.ledger, keydir, "approver-ben", held.change_id)
    with pytest.raises(ReleaseRefused, match="approver-ana launched this work and cannot approve"):
        do_release(env, held, keydir, tmp_path)


def test_release_a_rejection_blocks(env, held, keydir, tmp_path):
    approve(env.ledger, keydir, "approver-ben", held.change_id)
    approve(env.ledger, keydir, "approver-cy", held.change_id, "reject", "not convinced")
    with pytest.raises(ReleaseRefused, match="rejected by approver-cy"):
        do_release(env, held, keydir, tmp_path)


def test_release_approvals_do_not_carry_to_a_new_gate_result(env, held, keydir, tmp_path):
    """B6: approve, then the change is re-gated. The old approvals are for the old result."""
    approve(env.ledger, keydir, "approver-ben", held.change_id)
    approve(env.ledger, keydir, "approver-cy", held.change_id)
    old = env.ledger.find(fact_type="gate.result")[-1]
    env.ledger.append(sign_submission(
        keydir, "ci-gates", "gate.result", {**old.payload, "why": "re-run"}, list(old.refs)))
    with pytest.raises(ReleaseRefused, match="0 of 2 approvals"):
        do_release(env, held, keydir, tmp_path)


def test_release_refuses_a_blocked_or_missing_gate_and_a_second_release(env, scenario, keydir, tmp_path):
    with pytest.raises(ReleaseRefused, match="no gate result"):
        release("CHG-none", ledger=env.ledger, keydir=keydir, policy=env.policy,
                code_root=env.root, releases_dir=tmp_path / "r")
    with pytest.raises(ReleaseRefused, match="no gate result to approve"):
        approve(env.ledger, keydir, "approver-ben", "CHG-none")
    order, run = go(env, scenario, keydir, doubles.Liar, tower=False)
    assert run.outcome == "blocked"
    approve(env.ledger, keydir, "approver-ben", order.change_id)
    approve(env.ledger, keydir, "approver-cy", order.change_id)
    with pytest.raises(ReleaseRefused, match="the gate is blocked"):
        do_release(env, order, keydir, tmp_path)


def test_release_refuses_twice_and_refuses_on_a_damaged_ledger(env, held, keydir, tmp_path):
    import sqlite3

    approve(env.ledger, keydir, "approver-ben", held.change_id)
    approve(env.ledger, keydir, "approver-cy", held.change_id)
    do_release(env, held, keydir, tmp_path)
    with pytest.raises(ReleaseRefused, match="already been released"):
        do_release(env, held, keydir, tmp_path)
    conn = sqlite3.connect(env.ledger.store.path)
    conn.execute("DROP TRIGGER ledger_no_update")
    conn.execute("UPDATE ledger SET payload = replace(payload, 'pass', 'fail') WHERE seq = 5")
    conn.commit()
    conn.close()
    with pytest.raises(ReleaseRefused, match="the ledger is not intact"):
        do_release(env, held, keydir, tmp_path)


@pytest.mark.parametrize("identity", ["executor-recorded", "executor-loop", "advisory-reviewer",
                                      "ci-gates", "ci-release", "model-gateway"])
def test_only_approvers_approve(env, held, keydir, identity):
    """B7."""
    with pytest.raises(LedgerRejected) as caught:
        approve(env.ledger, keydir, identity, held.change_id)
    assert caught.value.code == "not-authoritative-writer"


def test_an_approver_cannot_write_a_gate_or_a_release(sqlite_ledger, keydir):
    for fact_type in ("gate.result", "release.approved", "release.manifest", "tower.result"):
        with pytest.raises(LedgerRejected):
            sqlite_ledger.append(sign_submission(keydir, "approver-ben", fact_type,
                                                 {"subject": "CHG-x"}))


def test_manifest_seals_what_was_released(env, held, keydir, tmp_path):
    """B8."""
    approve(env.ledger, keydir, "approver-ben", held.change_id)
    approve(env.ledger, keydir, "approver-cy", held.change_id)
    done = do_release(env, held, keydir, tmp_path)
    manifest = done.manifest
    assert manifest["change"] == "CHG-pd-0002" and manifest["spec"] == "SPEC-0002"
    assert manifest["tier"] == "C" and manifest["live"] is False
    assert manifest["skills"][0]["version"] == "1.1.0"
    assert set(manifest["files"]) == {"services/decision_income/__init__.py", CALC}
    assert len(manifest["approvals"]) == 2
    ids = {e.id: e.fact_type for e in env.ledger.entries()}
    assert {ids[i] for i in manifest["evidence"]} == {"handback.result", "tier.assigned", "check.result"}
    assert ids[manifest["gate_ref"]] == "gate.result"
    stored = tmp_path / "releases/decision-income/r1"
    assert (stored / CALC).read_text() == (held.worktree / CALC).read_text()
    assert json.loads((stored.parent / "channel.json").read_text())["current"] == "r1"
    assert verify_stored(tmp_path / "releases", "decision-income", "r1", env.ledger) == []


def test_manifest_detects_a_stored_release_that_was_changed(env, held, keydir, tmp_path):
    """B8."""
    approve(env.ledger, keydir, "approver-ben", held.change_id)
    approve(env.ledger, keydir, "approver-cy", held.change_id)
    do_release(env, held, keydir, tmp_path)
    stored = tmp_path / "releases/decision-income/r1"
    (stored / CALC).write_text((stored / CALC).read_text() + "\n# patched in place\n")
    (stored / "services/decision_income/extra.py").write_text("x = 1\n")
    problems = verify_stored(tmp_path / "releases", "decision-income", "r1", env.ledger)
    assert f"{CALC} differs from the sealed manifest" in problems
    assert any("extra.py is in the stored release and not in the manifest" in p for p in problems)
    assert verify_stored(tmp_path / "releases", "decision-income", "r9", env.ledger) == [
        "decision-income@r9 has no manifest in the ledger"]


# ---------------------------------------------------------------- degraded mode

def open_d(env, order, keydir, tmp_path, approvers=("approver-ben", "approver-cy")):
    return degraded.open_degraded(
        "CHG-urgent-1", "ledger store unreachable", [CALC], code_root=order.worktree,
        policy=env.policy, knowledge=env.knowledge, keydir=keydir, approvers=list(approvers),
        spool=tmp_path / "spool", now=NOW)


def test_degraded_path_and_backfill(env, held, keydir, tmp_path):
    """B9: gates run by hand, two signers, spool, back-fill."""
    path = open_d(env, held, keydir, tmp_path)
    record = json.loads(path.read_text())
    assert record["gates"] == {"paired-checks": {"ran": 5, "failed": []}, "fitness": {"violations": 0}}
    assert [a["approver"] for a in record["approvals"]] == ["approver-ben", "approver-cy"]
    assert degraded.overdue(tmp_path / "spool", NOW + timedelta(days=1)) == []
    assert degraded.overdue(tmp_path / "spool", NOW + timedelta(days=4)) == [
        f"CHG-urgent-1: back-fill was due {record['backfill_due']}"]

    entry = degraded.backfill(path, ledger=env.ledger, keydir=keydir, policy=env.policy,
                              now=NOW + timedelta(days=1))
    assert entry.fact_type == "degraded.backfilled" and entry.actor == "ci-release"
    assert entry.payload["degraded"] is True and entry.payload["late"] is False
    assert not path.exists() and (path.parent / "done" / path.name).exists()
    assert degraded.overdue(tmp_path / "spool", NOW + timedelta(days=30)) == []


def test_degraded_late_backfill_is_marked(env, held, keydir, tmp_path):
    path = open_d(env, held, keydir, tmp_path)
    entry = degraded.backfill(path, ledger=env.ledger, keydir=keydir, policy=env.policy,
                              now=NOW + timedelta(days=10))
    assert entry.payload["late"] is True


@pytest.mark.parametrize("approvers", [("approver-ben",), ("approver-ben", "approver-ben"),
                                       ("approver-ben", "executor-loop"), ("approver-ben", "nobody")])
def test_degraded_needs_two_different_approvers(env, held, keydir, tmp_path, approvers):
    with pytest.raises(degraded.DegradedRefused, match="needs 2 different approvers"):
        open_d(env, held, keydir, tmp_path, approvers)


def test_degraded_waives_nothing(env, scenario, keydir, tmp_path):
    """The code without the fix fails a paired check: degraded mode refuses it too."""
    order = build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                             root=env.root, policy=env.policy, knowledge=env.knowledge,
                             workdir=env.workdir)
    with pytest.raises(degraded.DegradedRefused, match="paired checks failed.*Nothing is waived"):
        open_d(env, order, keydir, tmp_path)
    assert not (tmp_path / "spool").exists()


def test_degraded_record_cannot_be_altered_in_the_spool(env, held, keydir, tmp_path):
    path = open_d(env, held, keydir, tmp_path)
    record = json.loads(path.read_text())
    record["changed"].append("policy/path-map.yaml")
    path.write_text(json.dumps(record))
    with pytest.raises(degraded.DegradedRefused, match="0 of 2 valid approver signatures"):
        degraded.backfill(path, ledger=env.ledger, keydir=keydir, policy=env.policy, now=NOW)
    assert env.ledger.find(fact_type="degraded.backfilled") == []
