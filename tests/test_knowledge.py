from __future__ import annotations

import shutil
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness import chain, ids
from harness.change import run_change
from harness.checks import governed_code_hash, run_skill_checks, skills_for_paths
from harness.knowledge import SkillInvalid, content_hash, lint, load_knowledge, publish, validate, verify
from harness.policy import PolicyError, load_policy
from services.decision_income import qualifying_monthly_income

SKILL = "SKL-income-calculation"


@pytest.fixture
def copy(root, tmp_path):
    """A private copy of the repository's governed content."""
    for name in ("policy", "knowledge", "services", "demo-keys"):
        shutil.copytree(root / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_repository_knowledge_is_whole(knowledge, policy):
    assert validate(knowledge, policy) == []
    for skill in knowledge.skills.values():
        verify(skill, policy)
        assert lint(skill.dir) == []


def test_id_kinds():
    assert ids.kind_of("PD-0001") == "product-decision"
    assert ids.kind_of("OBL-0001") == "obligation"
    assert ids.kind_of("RULE-0001") == "card" and ids.card_type("RULE-0001") == "RULE"
    assert ids.kind_of("SKL-income-calculation@1.0.0") == "skill-version"
    assert ids.kind_of("CHK-income-calculation-001") == "check"
    assert ids.kind_of("LED-12") == "ledger-entry"
    assert ids.kind_of("rule-1") is None


def test_skill_checks_all_pass_on_the_repository(knowledge, policy):
    results = run_skill_checks(knowledge.skills[SKILL], knowledge, policy)
    assert [r.passed for r in results] == [True] * 4
    covered = {c for r in results for c in r.covers}
    assert covered == {"FACT-0001", "RULE-0001", "CALC-0001"}


@pytest.mark.parametrize("edit", ["edit", "add", "remove"])
def test_any_change_to_a_released_skill_is_detected(copy, edit):
    skill_dir = copy / "knowledge/skills/income-calculation"
    before = content_hash(skill_dir)
    if edit == "edit":
        (skill_dir / "SKILL.md").write_text((skill_dir / "SKILL.md").read_text() + "\nextra\n")
    elif edit == "add":
        (skill_dir / "checks" / "extra.py").write_text("x = 1\n")
    else:
        (skill_dir / "checks" / "vectors.json").unlink()
    assert content_hash(skill_dir) != before
    knowledge = load_knowledge(copy)
    with pytest.raises(SkillInvalid, match="content changed since release"):
        verify(knowledge.skills[SKILL], load_policy(copy))


def test_release_signature_is_checked(copy):
    release = copy / "knowledge/skills/income-calculation/release.json"
    text = release.read_text()
    sig_start = text.index('"signature": "') + len('"signature": "')
    flipped = "0" if text[sig_start] != "0" else "1"
    release.write_text(text[:sig_start] + flipped + text[sig_start + 1:])
    knowledge = load_knowledge(copy)
    with pytest.raises(SkillInvalid, match="signature"):
        verify(knowledge.skills[SKILL], load_policy(copy))


def test_unreleased_skill_does_not_verify(copy):
    (copy / "knowledge/skills/income-calculation/release.json").unlink()
    knowledge = load_knowledge(copy)
    with pytest.raises(SkillInvalid, match="never released"):
        verify(knowledge.skills[SKILL], load_policy(copy))


@pytest.mark.parametrize(
    "text, expect",
    [
        ("import socket\n", "network or process"),
        ("from urllib import request\n", "network or process"),
        ("see https://example.test/x\n", "URL"),
        ("API_KEY = 'abc'\n", "credential"),
        ("eval('1+1')\n", "dynamic code"),
    ],
)
def test_lint_refuses_network_credentials_and_dynamic_code(copy, text, expect):
    skill_dir = copy / "knowledge/skills/income-calculation"
    (skill_dir / "checks" / "bad.py").write_text(text)
    assert any(expect in f for f in lint(skill_dir))
    knowledge = load_knowledge(copy)
    with pytest.raises(SkillInvalid, match="lint"):
        publish(knowledge.skills[SKILL], copy / "demo-keys")


@pytest.mark.parametrize(
    "path, old, new, expect",
    [
        ("knowledge/cards/RULE-0001.yaml", "moment: MOM-income-calculated", "moment: MOM-nowhere",
         "is not on a blueprint"),
        ("knowledge/cards/RULE-0001.yaml", "implements: [PD-0001, OBL-0001]", "implements: [PD-9999]",
         "unknown decision PD-9999"),
        ("knowledge/skills/income-calculation/manifest.yaml", "covers: [RULE-0001]", "covers: [FACT-0001]",
         "RULE-0001 has no paired deterministic check"),
        ("knowledge/skills/income-calculation/manifest.yaml", "  - card: PROC-0001", "  - card: PROC-9999",
         "PROC-0001 has no check and is not listed as pending"),
        ("knowledge/skills/income-calculation/manifest.yaml", "implements: [PD-0001, OBL-0001]",
         "implements: [OBL-0001]", "PD-0001: no skill implements this decision"),
        ("policy/skill-map.yaml", "services/decision_income/**", "services/gone/**", "matches no file"),
        ("policy/skill-map.yaml", "NODE-income-verify", "NODE-ghost", "is on no blueprint moment"),
        ("knowledge/blueprint/income-verification.yaml",
         "    data_classes: [consumer-personal, credit-decision]", "    data_classes: [made-up]",
         "unknown data class made-up"),
    ],
)
def test_broken_links_are_found(copy, path, old, new, expect):
    target = copy / path
    assert old in target.read_text()
    target.write_text(target.read_text().replace(old, new, 1))
    problems = validate(load_knowledge(copy), load_policy(copy))
    assert any(expect in p for p in problems), problems


@pytest.mark.parametrize(
    "path, old, new, expect",
    [
        ("policy/records-schedule.yaml", "floor_days: 1095", "floor_days: 9999", "floor is above ceiling"),
        ("policy/identities.yaml", "may_write: [tier.assigned]", "may_write: []", "exactly one authoritative"),
        ("policy/identities.yaml", "may_write: [change.proposed]", "may_write: [change.proposed, made.up]",
         "no record class"),
        ("policy/path-map.yaml", "tier: A", "tier: Z", "bad tier"),
    ],
)
def test_broken_policy_refuses_to_load(copy, path, old, new, expect):
    target = copy / path
    target.write_text(target.read_text().replace(old, new, 1))
    with pytest.raises(PolicyError, match=expect):
        load_policy(copy)


def test_shared_signing_key_refuses_to_load(copy):
    import yaml

    path = copy / "policy/identity-keys.yaml"
    keys = yaml.safe_load(path.read_text())
    keys["public_keys"]["ci-gates"] = keys["public_keys"]["agent-executor"]
    path.write_text(yaml.safe_dump(keys))
    with pytest.raises(PolicyError, match="share a signing key"):
        load_policy(copy)


def test_governed_code_hash_tracks_the_code(copy):
    policy = load_policy(copy)
    before = governed_code_hash(copy, policy, SKILL)
    calc = copy / "services/decision_income/calc.py"
    calc.write_text(calc.read_text() + "\n# touched\n")
    assert governed_code_hash(copy, policy, SKILL) != before


def test_skills_for_paths(policy):
    assert skills_for_paths(["services/decision_income/calc.py"], policy) == [SKILL]
    assert skills_for_paths(["knowledge/skills/income-calculation/SKILL.md"], policy) == [SKILL]
    assert skills_for_paths(["docs/a.md"], policy) == []


# ---------------------------------------------------- one change, three tiers

@pytest.mark.parametrize(
    "paths, tier, outcome, checks",
    [
        (["docs/a.md"], "A", "passed", 0),
        (["harness/cli.py"], "B", "passed", 0),
        (["services/decision_income/calc.py"], "C", "held-for-approval", 4),
    ],
)
def test_one_pipeline_three_tiers(sqlite_ledger, keydir, policy, knowledge, paths, tier, outcome, checks):
    run = run_change("CHG-t", paths, ledger=sqlite_ledger, keydir=keydir,
                     policy=policy, knowledge=knowledge)
    assert (run.tier, run.outcome) == (tier, outcome)
    by_type = [e.fact_type for e in sqlite_ledger.entries()]
    assert by_type[:2] == ["change.proposed", "tier.assigned"] and by_type[-1] == "gate.result"
    assert by_type.count("check.result") == checks
    writers = {e.fact_type: e.actor for e in sqlite_ledger.entries()}
    assert writers["change.proposed"] == "agent-executor"
    assert writers["tier.assigned"] == "ci-tiering"
    assert writers["gate.result"] == "ci-gates"
    assert sqlite_ledger.verify() == [] and sqlite_ledger.reconcile() == []


def test_unclassifiable_change_is_blocked_and_reconciliation_sees_it(sqlite_ledger, keydir, policy, knowledge):
    run = run_change("CHG-u", ["knowledge/skills/ghost/SKILL.md"], ledger=sqlite_ledger,
                     keydir=keydir, policy=policy, knowledge=knowledge)
    assert run.outcome == "blocked" and run.tier is None
    assert sqlite_ledger.reconcile() == ["CHG-u: has change.proposed but no tier.assigned"]


def test_drifted_code_is_blocked_with_recorded_failures(copy, tmp_path):
    from harness.ledger import FileAnchor, Ledger
    from harness.ledger.store import SqliteStore

    calc = copy / "services/decision_income/calc.py"
    calc.write_text(calc.read_text().replace(
        '"biweekly": 26', '"biweekly": 24'))
    policy, knowledge = load_policy(copy), load_knowledge(copy)
    store = SqliteStore(tmp_path / "l" / "ledger.db")
    store.init()
    ledger = Ledger(store, policy, FileAnchor(tmp_path / "l" / "anchor.json"))
    run = run_change("CHG-d", ["services/decision_income/calc.py"], ledger=ledger,
                     keydir=copy / "demo-keys", policy=policy, knowledge=knowledge)
    assert run.outcome == "blocked"
    outcomes = {e.payload["subject"]: e.payload["outcome"]
                for e in ledger.find(fact_type="check.result")}
    assert outcomes["CHK-income-calculation-001"] == "fail"
    assert outcomes["CHK-income-calculation-003"] == "fail"
    assert ledger.find(fact_type="gate.result")[0].payload["outcome"] == "blocked"


def test_checks_ignore_stale_bytecode(copy):
    """An edit that keeps a file's size and timestamp must not hide behind an old .pyc."""
    import os
    import py_compile

    calc = copy / "services/decision_income/calc.py"
    stat = calc.stat()
    py_compile.compile(str(calc))  # bytecode for the correct code, in __pycache__
    calc.write_text(calc.read_text().replace(
        "VARIABLE_HISTORY_MIN_MONTHS = 24", "VARIABLE_HISTORY_MIN_MONTHS = 12"))
    os.utime(calc, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert calc.stat().st_size == stat.st_size and calc.stat().st_mtime_ns == stat.st_mtime_ns

    policy, knowledge = load_policy(copy), load_knowledge(copy)
    results = run_skill_checks(knowledge.skills[SKILL], knowledge, policy)
    failed = {r.check_id for r in results if not r.passed}
    assert "CHK-income-calculation-002" in failed


def test_a_runner_that_cannot_run_fails_every_check(copy):
    check = copy / "knowledge/skills/income-calculation/checks/check_income.py"
    check.write_text("this is not python (\n" + check.read_text())
    policy, knowledge = load_policy(copy), load_knowledge(copy)
    publish(knowledge.skills[SKILL], copy / "demo-keys")
    results = run_skill_checks(knowledge.skills[SKILL], knowledge, policy)
    assert len(results) == 4 and not any(r.passed for r in results)


def test_chain_forward_and_back(sqlite_ledger, keydir, policy, knowledge):
    before = chain.forward("PD-0001", knowledge, policy, sqlite_ledger)
    assert before["skills"][0]["checks"][0]["latest"] is None
    run_change("CHG-c", ["services/decision_income/calc.py"], ledger=sqlite_ledger,
               keydir=keydir, policy=policy, knowledge=knowledge)
    after = chain.forward("PD-0001", knowledge, policy, sqlite_ledger)
    skill = after["skills"][0]
    assert skill["skill"] == "SKL-income-calculation@1.0.0"
    assert all(c["latest"]["outcome"] == "pass" for c in skill["checks"])
    assert "RULE-0001" in after["cards"]
    assert "PASS in LED-" in chain.render_forward(after)

    back = chain.why("services/decision_income/calc.py", knowledge, policy)
    assert back["tier"] == "C"
    assert "PD-0001" in back["skills"][0]["decisions"]
    assert chain.why("docs/a.md", knowledge, policy)["skills"] == []
    with pytest.raises(KeyError):
        chain.forward("PD-9999", knowledge, policy, sqlite_ledger)


# ------------------------------------------------- the decision service itself

money = st.decimals(min_value=0, max_value=Decimal("1000000"), places=2)
frequency = st.sampled_from(["weekly", "biweekly", "semimonthly", "monthly"])


@settings(max_examples=300, deadline=None)
@given(money, frequency, st.integers(0, 23), money)
def test_short_history_never_adds_variable_income(base, freq, months, variable):
    result = qualifying_monthly_income(base, freq, months, variable)
    assert result.variable_monthly == 0
    assert result.qualifying_monthly == result.base_monthly


@settings(max_examples=300, deadline=None)
@given(money, money, frequency, st.integers(0, 120), money)
def test_more_base_pay_never_lowers_the_result(a, b, freq, months, variable):
    low, high = sorted([a, b])
    assert (qualifying_monthly_income(low, freq, months, variable).qualifying_monthly
            <= qualifying_monthly_income(high, freq, months, variable).qualifying_monthly)


@settings(max_examples=300, deadline=None)
@given(money, frequency, st.integers(0, 120), money)
def test_result_is_exact_cents_and_adds_up(base, freq, months, variable):
    result = qualifying_monthly_income(base, freq, months, variable)
    assert result.qualifying_monthly == result.base_monthly + result.variable_monthly
    assert result.qualifying_monthly == result.qualifying_monthly.quantize(Decimal("0.01"))
    assert result.qualifying_monthly >= 0
    assert ("RULE-0001" in result.cards_applied) == (variable > 0)


@pytest.mark.parametrize(
    "args",
    [
        (Decimal("-1"), "monthly", 0, Decimal("0")),
        (Decimal("1"), "monthly", 0, Decimal("-1")),
        (Decimal("1"), "monthly", -1, Decimal("0")),
        (Decimal("1"), "fortnightly", 0, Decimal("0")),
    ],
)
def test_bad_input_is_refused_not_guessed(args):
    with pytest.raises(ValueError):
        qualifying_monthly_income(*args)
