"""Stage 4, tasks 1 to 6 of SPEC-0001: specs, roles, work orders, hand-back
validation, the recorded executor and the conformance suite.

Test names follow the acceptance criteria in the spec where one applies.
No model runs anywhere in this file.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace

import pytest

from attacks import executors as doubles
from conformance.scenario import load_scenario, prepare
from conformance.suite import deliver, open_env, render, run_suite
from executors import load as load_executor
from executors.recorded import RecordedExecutor
from harness import chain, trailers
from harness.checks import run_skill_checks
from harness.knowledge import load_knowledge, validate
from harness.ledger import LedgerRejected, sign_submission
from harness.policy import load_policy
from harness.workorder import (
    WorkOrderRefused,
    build_work_order,
    derive_changes,
    snapshot,
)

RED = "CHK-income-calculation-005"


@pytest.fixture(scope="module")
def scenario(root):
    return load_scenario(root, "pd-0002")


@pytest.fixture
def env(scenario, root, tmp_path):
    return open_env(scenario, root, tmp_path)


def make(cls, scenario):
    return cls(doubles.recording(scenario.dir))


def run(env, scenario, keydir, cls=RecordedExecutor, **kw):
    executor = make(cls, scenario)
    order, delivery = deliver(env, scenario, executor, keydir, **kw)
    return executor, order, delivery


def facts(env):
    return [e.fact_type for e in env.ledger.entries()]


# ------------------------------------------------------------ specs (task 1, 2)

def test_specs_load_with_front_matter(knowledge):
    spec = knowledge.specs["SPEC-0001"]
    assert spec.status == "approved"
    assert "ADR-002" in spec.cites and "harness/**" in spec.allowed_paths
    assert "services/**" not in spec.allowed_paths


@pytest.mark.parametrize(
    "old, new, expect",
    [
        ("cites: [PD-0002, OBL-0001]", "cites: [PD-0002, PD-9999]", "cites PD-9999, which is not in the register"),
        ("cites: [PD-0002, OBL-0001]", "cites: []", "cites no decision"),
        ("status: approved", "status: maybe", "status 'maybe'"),
        ('allowed_paths:\n  - "services/decision_income/**"', "allowed_paths: []", "states no allowed paths"),
        ("id: SPEC-0002", "id: SPEC-2", "does not start with SPEC-2"),
    ],
)
def test_a_broken_spec_fails_validation(env, old, new, expect):
    path = env.root / "specs/SPEC-0002-declining-variable-income.md"
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, new, 1))
    problems = validate(load_knowledge(env.root), env.policy)
    assert any(expect in p for p in problems), problems


def test_a_decision_nothing_implements_or_cites_fails(env):
    (env.root / "knowledge/decisions/ADR-099.yaml").write_text("id: ADR-099\ntitle: orphan\n")
    problems = validate(load_knowledge(env.root), env.policy)
    assert "ADR-099: no skill implements this decision and no spec cites it" in problems


# ------------------------------------------------------------------ roles (task 3)

def test_two_identities_can_hold_the_executor_role(policy):
    assert policy.identities_in_role("executor") == [
        "agent-executor", "executor-cli", "executor-loop", "executor-recorded"]
    assert policy.writer_role_of("change.proposed") == "executor"
    assert policy.may_write("executor-recorded", "change.proposed")
    assert not policy.may_write("executor-recorded", "gate.result")
    assert not policy.may_write("nobody", "change.proposed")


@pytest.mark.parametrize("fact_type", ["gate.result", "check.result", "tier.assigned",
                                       "handback.result", "evidence.mismatch", "conformance.result"])
def test_no_executor_identity_can_write_a_control_fact(sqlite_ledger, keydir, policy, fact_type):
    for identity in policy.identities_in_role("executor"):
        with pytest.raises(LedgerRejected) as caught:
            sqlite_ledger.append(sign_submission(keydir, identity, fact_type, {"subject": "CHG-x"}))
        assert caught.value.code == "not-authoritative-writer"


def test_one_executor_cannot_sign_as_another(sqlite_ledger, keydir):
    forged = sign_submission(keydir, "executor-recorded", "change.proposed",
                             {"subject": "CHG-x"}, key_of="agent-executor")
    with pytest.raises(LedgerRejected) as caught:
        sqlite_ledger.append(forged)
    assert caught.value.code == "bad-signature"


# ------------------------------------------------------------ work orders (task 4)

def test_scenario_starts_red_and_whole(env, scenario):
    assert validate(env.knowledge, env.policy) == []
    skill = env.knowledge.skills["SKL-income-calculation"]
    assert skill.version == "1.1.0"
    results = {r.check_id: r.passed for r in run_skill_checks(skill, env.knowledge, env.policy)}
    assert results[RED] is False
    assert [k for k, v in results.items() if v] == [f"CHK-income-calculation-00{i}" for i in range(1, 5)]


def test_work_order_contents(env, scenario, keydir):
    order = build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                             root=env.root, policy=env.policy, knowledge=env.knowledge,
                             workdir=env.workdir)
    assert order.allowed_paths == ("services/decision_income/**",)
    assert [s.ref for s in order.skills] == ["SKL-income-calculation@1.1.0"]
    assert "PD-0002" in order.task
    assert not (order.worktree / "demo-keys").exists()
    assert not (order.worktree / ".git").exists()
    assert (order.worktree / "services/decision_income/calc.py").exists()
    assert order.manifest == snapshot(order.worktree) and derive_changes(order) == []
    with pytest.raises(WorkOrderRefused, match="used once"):
        build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                         root=env.root, policy=env.policy, knowledge=env.knowledge,
                         workdir=env.workdir)


@pytest.mark.parametrize(
    "kwargs, expect",
    [
        ({"change_id": "not-an-id"}, "not a valid change id"),
        ({"spec_id": "SPEC-9999"}, "not a spec"),
        ({"spec_id": "SPEC-0001", "executor": "ci-gates"}, "does not hold the executor role"),
        ({"executor": "nobody"}, "does not hold the executor role"),
    ],
)
def test_work_order_refused(env, scenario, kwargs, expect):
    args = {"change_id": scenario.change, "spec_id": scenario.spec, "executor": "executor-recorded"}
    args.update(kwargs)
    with pytest.raises(WorkOrderRefused, match=expect):
        build_work_order(args["change_id"], args["spec_id"], executor=args["executor"],
                         root=env.root, policy=env.policy, knowledge=env.knowledge,
                         workdir=env.workdir)


def test_work_order_refused_for_an_unapproved_spec(env, scenario):
    path = env.root / "specs/SPEC-0002-declining-variable-income.md"
    path.write_text(path.read_text().replace("status: approved", "status: draft"))
    with pytest.raises(WorkOrderRefused, match="is draft, not approved"):
        build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                         root=env.root, policy=env.policy, knowledge=load_knowledge(env.root),
                         workdir=env.workdir)


def test_work_order_refused_when_a_skill_does_not_verify(env, scenario):
    skill_md = env.root / "knowledge/skills/income-calculation/SKILL.md"
    skill_md.write_text(skill_md.read_text() + "\nedited after release\n")
    with pytest.raises(WorkOrderRefused, match="content changed since release"):
        build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                         root=env.root, policy=env.policy, knowledge=load_knowledge(env.root),
                         workdir=env.workdir)


# ------------------------------------------------ the contract clauses (A2 to A7)

def test_clause_skill_map_load(env, scenario, keydir):
    _, order, delivery = run(env, scenario, keydir)
    assert delivery.verdict.accepted
    _, _, bad = run(open_env(scenario, env.root, env.workdir / "second"),
                    scenario, keydir, doubles.WrongSkill)
    assert bad.outcome == "blocked"
    assert any("skills loaded do not match the work order" in v for v in bad.verdict.violations)


def test_clause_skill_canary(env, scenario, keydir, tmp_path):
    _, order, delivery = run(env, scenario, keydir, doubles.ReadsSkill, with_canary=True)
    assert order.canary and delivery.verdict.accepted
    # The released skill in the base repository is untouched by the token.
    assert order.canary not in (env.root / "knowledge/skills/income-calculation/SKILL.md").read_text()

    second = open_env(scenario, env.root, tmp_path / "second")
    _, order2, blind = run(second, scenario, keydir, RecordedExecutor, with_canary=True)
    assert blind.outcome == "blocked"
    assert any("one-time skill token" in v for v in blind.verdict.violations)
    assert order2.canary != order.canary  # one-time: a token from another run is useless


def test_clause_deny_list(env, scenario, keydir, tmp_path):
    before = snapshot(env.root)
    for index, cls in enumerate([doubles.EditsPolicy, doubles.EditsSkillCheck,
                                 doubles.PlantsHarness, doubles.AddsSymlink]):
        fresh = open_env(scenario, env.root, tmp_path / f"e{index}")
        _, order, delivery = run(fresh, scenario, keydir, cls)
        assert delivery.outcome == "blocked", cls.__name__
        assert not delivery.verdict.accepted
        assert "check.result" not in facts(fresh), "checks must not run on a rejected hand-back"
        assert fresh.ledger.find(fact_type="handback.result")[0].payload["accepted"] is False
        assert snapshot(fresh.root) == snapshot(env.root), "the base repository must not change"
    assert snapshot(env.root) == before


def test_allowed_paths_bind_even_off_the_deny_list(env, scenario, keydir):
    """README.md and tests/ are on no deny list. The spec does not allow them, so: rejected."""
    assert not any(g.startswith(("README", "tests")) for g in env.policy.deny_write)
    _, _, delivery = run(env, scenario, keydir, doubles.EditsReadme)
    assert delivery.outcome == "blocked"
    outside = [v for v in delivery.verdict.violations if "outside the paths the spec allows" in v]
    assert {v.split(":")[0] for v in outside} == {"README.md", "tests/test_new.py"}


def test_deny_list_binds_even_when_a_spec_allows_everything(env, scenario, keydir):
    """An over-broad spec does not make policy/ or the skill writable."""
    path = env.root / "specs/SPEC-0002-declining-variable-income.md"
    path.write_text(path.read_text().replace('  - "services/decision_income/**"', '  - "**"'))
    wide = replace(env, knowledge=load_knowledge(env.root))
    assert wide.knowledge.specs["SPEC-0002"].allowed_paths == ["**"]
    _, _, delivery = run(wide, scenario, keydir, doubles.EditsPolicy)
    assert delivery.outcome == "blocked"
    assert delivery.verdict.violations == ["policy/path-map.yaml: on the deny list"]


def test_a_symlink_inside_the_allowed_paths_is_rejected(env, scenario, keydir):
    _, _, delivery = run(env, scenario, keydir, doubles.AddsSymlink)
    assert delivery.outcome == "blocked"
    assert delivery.verdict.violations == [
        "services/decision_income/keys: symbolic links are not accepted"]


def test_clause_handback_diff(env, scenario, keydir, tmp_path):
    _, order, delivery = run(env, scenario, keydir, doubles.Liar)
    # The executor claimed it touched nothing. The harness derived what it did touch.
    assert delivery.verdict.changed == ["services/decision_income/calc.py"]
    recorded_verdict = env.ledger.find(fact_type="handback.result")[0].payload
    assert recorded_verdict["derived_changes"] == ["services/decision_income/calc.py"]

    nothing = open_env(scenario, env.root, tmp_path / "n")
    _, _, empty = run(nothing, scenario, keydir, doubles.ChangesNothing)
    assert empty.outcome == "blocked" and "changes nothing" in empty.verdict.violations[0]


def test_clause_rederived_evidence(env, scenario, keydir):
    _, order, delivery = run(env, scenario, keydir, doubles.Liar)
    assert delivery.outcome == "blocked" and delivery.tier == "C"
    assert delivery.check_outcomes[RED] == "fail"
    mismatch = env.ledger.find(fact_type="evidence.mismatch")
    assert len(mismatch) == 1
    text = " ".join(mismatch[0].payload["mismatches"])
    assert f"{RED}: executor claimed pass, CI got fail" in text
    assert "files: executor claimed []" in text
    assert "tier='A'" in text and "skip_checks=True" in text
    # The claim is on the record as a claim, written by the executor. Results are CI's.
    writers = {e.fact_type: env.policy.identities[e.actor].role for e in env.ledger.entries()}
    assert writers["executor.evidence"] == "executor"
    assert writers["check.result"] == writers["gate.result"] == "gates"
    assert writers["tier.assigned"] == "tiering"
    assert env.ledger.verify() == [] and env.ledger.reconcile() == []


def test_checks_use_the_released_skill_not_the_worktree_copy(env, scenario, keydir):
    """Even if a weakened check slipped through, CI runs the base repository's skill."""
    order = build_work_order(scenario.change, scenario.spec, executor="executor-recorded",
                             root=env.root, policy=env.policy, knowledge=env.knowledge,
                             workdir=env.workdir)
    check = order.worktree / doubles.CHECK
    check.write_text(check.read_text()
                     + "\n\ndef check_declining_variable_income_rule(ctx):\n    return None\n")
    (order.worktree / "harness" / "check_runner.py").write_text("print('[]')\n")
    results = {r.check_id: r.passed for r in run_skill_checks(
        env.knowledge.skills["SKL-income-calculation"], env.knowledge, env.policy,
        code_root=order.worktree)}
    assert results[RED] is False and len(results) == 5


def test_clause_trailers(env, scenario, keydir):
    executor, order, delivery = run(env, scenario, keydir)
    changed = delivery.verdict.changed
    message = f"Implement PD-0002\n\n{delivery.trailers}\n"
    assert trailers.check(message, changed, env.knowledge, env.policy) == []
    parsed = trailers.parse(message)
    assert parsed["Spec"] == ["SPEC-0002"] and parsed["Change"] == ["CHG-pd-0002"]
    assert parsed["Executor"] == ["recorded@1 (recorded)"]

    def problems(text):
        return trailers.check(text, changed, env.knowledge, env.policy)

    lines = delivery.trailers.splitlines()

    def without(key):
        return "subject\n\n" + "\n".join(line for line in lines if not line.startswith(key))

    assert "needs exactly one Spec trailer" in problems(without("Spec"))[0]
    assert any("Change trailer" in p for p in problems(without("Change")))
    assert any("Executor trailer" in p for p in problems(without("Executor")))
    assert any("is not in Skills-Loaded" in p for p in problems(without("Skills-Loaded")))
    assert any("not a spec" in p for p in problems(message.replace("SPEC-0002", "SPEC-9999")))
    assert any("is not the released skill" in p
               for p in problems(message.replace("@1.1.0#", "@1.0.0#")))
    assert any("is not the released skill" in p
               for p in problems(message.replace(order.skills[0].content_hash[:12], "0" * 12)))
    outside = trailers.check(message, changed + ["policy/path-map.yaml"], env.knowledge, env.policy)
    assert any("outside the paths SPEC-0002 allows" in p for p in outside)


def test_commit_check_over_real_history(env, scenario, keydir, tmp_path):
    """A commit without a spec, or outside its spec, fails. A full one passes."""
    executor, order, delivery = run(env, scenario, keydir)
    repo = env.root

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.test")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "baseline")
    baseline = git("rev-parse", "HEAD")
    policy = replace(env.policy, enforce_commits_after=baseline)

    shutil.copyfile(order.worktree / "services/decision_income/calc.py",
                    repo / "services/decision_income/calc.py")
    git("commit", "-qam", f"Implement PD-0002\n\n{delivery.trailers}")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs/note.md").write_text("x")
    git("add", "-A")
    git("commit", "-qm", "docs with no spec")
    (repo / "services/decision_income/calc.py").write_text(
        (repo / "services/decision_income/calc.py").read_text() + "\n# drift\n")
    git("commit", "-qam", "tweak\n\nSpec: SPEC-0002")

    results = trailers.check_commits(repo, env.knowledge, policy)
    assert [bool(problems) for _, problems in results] == [False, True, True]
    assert "needs exactly one Spec trailer" in results[1][1][0]
    assert any("Change trailer" in p for p in results[2][1])

    unknown = replace(env.policy, enforce_commits_after="0" * 40)
    assert "baseline commit is not in this history" in trailers.check_commits(
        repo, env.knowledge, unknown)[0][1][0]


def test_this_repository_has_a_commit_baseline(policy):
    assert policy.enforce_commits_after and len(policy.enforce_commits_after) == 40


# ------------------------------------------------------- failure modes of a run

def test_an_executor_that_crashes_ends_in_a_blocked_gate(env, scenario, keydir):
    _, _, delivery = run(env, scenario, keydir, doubles.Crashes)
    assert delivery.outcome == "blocked"
    assert facts(env) == ["change.proposed", "gate.result"]
    assert "executor failed: RuntimeError" in env.ledger.find(fact_type="gate.result")[0].payload["why"]
    assert env.ledger.reconcile() == []


def test_personal_data_in_executor_evidence_blocks_the_change(env, scenario, keydir):
    _, _, delivery = run(env, scenario, keydir, doubles.PersonalDataInEvidence)
    assert delivery.outcome == "blocked"
    assert "executor.evidence" not in facts(env)
    assert "personal-data-in-payload" in env.ledger.find(fact_type="gate.result")[0].payload["why"]


def test_an_executor_cannot_run_someone_elses_work_order(env, scenario, keydir):
    order = build_work_order(scenario.change, scenario.spec, executor="agent-executor",
                             root=env.root, policy=env.policy, knowledge=env.knowledge,
                             workdir=env.workdir)
    from harness.delivery import run_work_order

    delivery = run_work_order(order, make(RecordedExecutor, scenario), ledger=env.ledger,
                              keydir=keydir, policy=env.policy, knowledge=env.knowledge)
    assert delivery.outcome == "blocked" and env.ledger.entries() == []


def test_a_stale_recording_cannot_pass(env, scenario, keydir):
    """Re-release the skill after the recording was made: the recorded claim is now wrong."""
    from harness.knowledge import publish

    skill_md = env.root / "knowledge/skills/income-calculation/SKILL.md"
    skill_md.write_text(skill_md.read_text() + "\nA later clarification from the SME.\n")
    publish(load_knowledge(env.root).skills["SKL-income-calculation"], keydir)
    fresh = replace(env, knowledge=load_knowledge(env.root))
    _, _, delivery = run(fresh, scenario, keydir)
    assert delivery.outcome == "blocked"
    assert any("skills loaded do not match" in v for v in delivery.verdict.violations)


# ---------------------------------------------- the happy path and the suite

def test_recorded_run_turns_red_to_green(env, scenario, keydir):
    _, order, delivery = run(env, scenario, keydir)
    assert (delivery.tier, delivery.outcome) == ("C", "held-for-approval")
    assert delivery.check_outcomes == {f"CHK-income-calculation-00{i}": "pass" for i in range(1, 6)}
    assert delivery.mismatches == []
    assert facts(env) == ["change.proposed", "executor.evidence", "handback.result",
                          "tier.assigned"] + ["check.result"] * 5 + ["gate.result"]
    assert env.ledger.verify() == [] and env.ledger.reconcile() == []


def test_chain_is_closed_through_change(env, scenario, keydir):
    before = chain.forward("PD-0002", env.knowledge, env.policy, env.ledger)
    assert any("no change with a gate result" in o for o in before["open"])
    run(env, scenario, keydir)
    data = chain.forward("PD-0002", env.knowledge, env.policy, env.ledger)
    assert data["open"] == []
    assert data["specs"][0]["spec"] == "SPEC-0002"
    change = data["specs"][0]["changes"][0]
    assert (change["change"], change["outcome"], change["recorded"]) == (
        "CHG-pd-0002", "held-for-approval", True)
    text = chain.render_forward(data)
    assert "spec SPEC-0002" in text and "change CHG-pd-0002 by recorded (RECORDED)" in text
    assert f"{RED} covers RULE-0002: PASS in LED-" in text


def test_a_decision_that_predates_specs_says_so(knowledge, policy, sqlite_ledger):
    data = chain.forward("PD-0001", knowledge, policy, sqlite_ledger)
    assert "no spec cites this decision" in data["open"]


def test_recorded_executor_is_labelled(env, scenario, keydir):
    executor, _, delivery = run(env, scenario, keydir)
    assert executor.kind == "recorded"
    for fact_type in ("change.proposed", "executor.evidence", "handback.result", "gate.result"):
        assert env.ledger.find(fact_type=fact_type)[0].payload["recorded"] is True
    assert env.ledger.find(fact_type="executor.evidence")[0].payload["claim"]["model"] is None
    assert "(recorded)" in delivery.trailers
    assert any("RECORDED executor, no live model ran" in s for s in delivery.steps)
    assert env.ledger.find(fact_type="gate.result")[0].payload["live"] is False


def test_conformance_suite_on_the_recorded_executor(scenario, root, keydir, tmp_path):
    suite = run_suite("recorded", scenario, root, keydir, tmp_path, swap_with="loop")
    status = {c.criterion: c.status for c in suite.clauses}
    assert [k for k, v in status.items() if v == "fail"] == []
    assert {k for k, v in status.items() if v == "n/a"} == {"A3", "A8", "A11"}
    assert {"A2", "A4", "A5", "A6", "A7", "A9", "A10", "A12", "A13"} <= {
        k for k, v in status.items() if v == "pass"}
    # A recorded executor is never called conformant.
    assert suite.verdict.startswith("passes 13 of 16")
    assert "RECORDED: the harness side only" in suite.verdict
    assert "RECORDED: no model ran" in render(suite)
    result = suite.ledger.find(fact_type="conformance.result")[0]
    assert result.actor == "ci-conformance" and result.payload["live"] is False


def test_conformance_suite_fails_a_misbehaving_executor(scenario, root, keydir, tmp_path):
    suite = run_suite("liar", scenario, root, keydir, tmp_path,
                      make_executor=lambda env: doubles.Liar(doubles.recording(env.scenario_dir)))
    failed = {c.criterion for c in suite.clauses if c.status == "fail"}
    assert {"A7", "A12", "outcome"} <= failed
    assert suite.verdict == "does not conform"


def test_unknown_executor_names_what_exists(scenario):
    with pytest.raises(KeyError, match="Available: recorded, loop"):
        load_executor("nope", scenario.dir)


def test_scenario_overlay_does_not_touch_the_repository(scenario, root, tmp_path):
    before = snapshot(root)
    prepare(scenario, root, tmp_path / "repo")
    assert snapshot(root) == before
    assert load_knowledge(root).skills["SKL-income-calculation"].version == "1.0.0"
    assert "PD-0002" not in load_knowledge(root).decisions
