from __future__ import annotations

import subprocess

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness.tiering import TierCheckFailed, classify, glob_to_regex, normalise
from harness.tiering.retro import parse_name_status, retro
from harness.tiering.rulebook import RANK

KNOWN = [
    "docs/guide.md",
    "README.md",
    "tests/test_x.py",
    "harness/cli.py",
    "harness/ledger/api.py",
    "harness/tiering/rulebook.py",
    "policy/tier-rules.yaml",
    "knowledge/cards/RULE-0001.yaml",
    "knowledge/skills/income-calculation/SKILL.md",
    "services/decision_income/calc.py",
    "somewhere/unmapped.py",
    "Docs/guide.md",
    "docs/../policy/identities.yaml",
]
paths = st.lists(st.sampled_from(KNOWN), min_size=1, max_size=6)


@pytest.mark.parametrize(
    "path, tier",
    [
        ("docs/guide.md", "A"),
        ("README.md", "A"),
        ("tests/test_x.py", "B"),
        ("harness/cli.py", "B"),
        ("harness/ledger/api.py", "C"),
        ("harness/tiering/rulebook.py", "C"),
        ("harness/gates.py", "C"),
        ("policy/tier-rules.yaml", "C"),
        (".github/workflows/ci.yml", "C"),
        ("knowledge/blueprint/income-verification.yaml", "B"),
        ("knowledge/cards/RULE-0001.yaml", "C"),
        ("knowledge/skills/income-calculation/SKILL.md", "C"),
        ("services/decision_income/calc.py", "C"),
    ],
)
def test_mapped_paths(policy, path, tier):
    assert classify([path], policy).tier == tier


@pytest.mark.parametrize(
    "path",
    ["somewhere/new.py", "Docs/guide.md", "services2/calc.py", "../outside.py",
     "/etc/passwd", "C:\\repo\\docs\\x.md", "docs/../../x", "readme.md"],
)
def test_unmapped_or_escaping_paths_are_tier_c_by_default(policy, path):
    decision = classify([path], policy)
    assert decision.tier == "C"
    assert decision.by_default is True


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("docs/../services/decision_income/calc.py", "services/decision_income/calc.py"),
        ("./docs/x.md", "docs/x.md"),
        ("docs\\x.md", "docs/x.md"),
        ("docs//x.md", "docs/x.md"),
        ("../x", None),
        ("/abs", None),
        ("", None),
        (".", None),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_traversal_cannot_hide_a_regulated_path(policy):
    decision = classify(["docs/a.md", "docs/../services/decision_income/calc.py"], policy)
    assert decision.tier == "C" and decision.by_default is False


def test_mixed_change_takes_the_highest_tier(policy):
    assert classify(["docs/a.md", "harness/cli.py"], policy).tier == "B"
    assert classify(["docs/a.md", "harness/cli.py", "policy/x.yaml"], policy).tier == "C"


def test_data_class_can_raise_a_rule(policy):
    from dataclasses import replace

    raised = replace(policy, path_map=({"glob": "x/**", "tier": "A",
                                        "data_classes": ["consumer-personal"]},))
    decision = classify(["x/file.py"], raised)
    assert decision.tier == "C"
    assert "raises it to Tier C" in decision.findings[0].reasons[0]


def test_unknown_data_class_fails_the_check(policy):
    from dataclasses import replace

    broken = replace(policy, path_map=({"glob": "x/**", "tier": "A", "data_classes": ["mystery"]},))
    with pytest.raises(TierCheckFailed):
        classify(["x/file.py"], broken)


def test_obligation_reference_sets_a_floor(policy):
    assert classify(["docs/a.md"], policy).tier == "A"
    decision = classify(["docs/a.md"], policy, ["OBL-0001"])
    assert decision.tier == "C" and decision.by_default is False
    assert "OBL-0001" in decision.reasons[0]


def test_skill_tier_comes_from_consuming_nodes(policy):
    from dataclasses import replace

    quiet = replace(policy, skill_map=({"id": "SKL-x", "path": "knowledge/skills/x/**",
                                        "consuming_nodes": [{"node": "NODE-a", "regulated": False}]},))
    assert classify(["knowledge/skills/x/SKILL.md"], quiet).tier == "B"


def test_skill_without_map_entry_or_nodes_fails(policy):
    from dataclasses import replace

    with pytest.raises(TierCheckFailed, match="no skill-map entry"):
        classify(["knowledge/skills/unknown/SKILL.md"], policy)
    empty = replace(policy, skill_map=({"id": "SKL-x", "path": "knowledge/skills/x/**",
                                        "consuming_nodes": []},))
    with pytest.raises(TierCheckFailed, match="no known consuming nodes"):
        classify(["knowledge/skills/x/SKILL.md"], empty)


def test_empty_change_fails(policy):
    with pytest.raises(TierCheckFailed):
        classify([], policy)


def test_payload_carries_reasons_and_rulebook_hash(policy):
    payload = classify(["docs/a.md", "x/y.py"], policy).to_payload("CHG-1")
    assert payload["subject"] == "CHG-1" and payload["tier"] == "C"
    assert payload["rulebook_hash"] == policy.rulebook_hash()
    assert {f["path"] for f in payload["findings"]} == {"docs/a.md", "x/y.py"}
    assert "tier" not in {k for k in payload if k.startswith("declared")}


@pytest.mark.parametrize(
    "glob, path, match",
    [
        ("docs/**", "docs/a/b/c.md", True),
        ("docs/**", "docs", False),
        ("docs/**", "docsx/a.md", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("a/**/b.py", "a/b.py", True),
        ("a/**/b.py", "a/x/y/b.py", True),
        ("a/?.py", "a/b.py", True),
        ("a/?.py", "a/bc.py", False),
        ("harness/gates.py", "harness/gatesXpy", False),
    ],
)
def test_glob_semantics(glob, path, match):
    assert bool(glob_to_regex(glob).match(path)) is match


@settings(max_examples=200, deadline=None)
@given(paths, st.randoms())
def test_order_does_not_matter(policy, chosen, rnd):
    shuffled = list(chosen)
    rnd.shuffle(shuffled)
    assert classify(chosen, policy).tier == classify(shuffled, policy).tier


@settings(max_examples=200, deadline=None)
@given(paths, paths)
def test_adding_paths_never_lowers_the_tier(policy, base, extra):
    before = classify(base, policy).tier
    after = classify(base + extra, policy).tier
    assert RANK[after] >= RANK[before]


@settings(max_examples=200, deadline=None)
@given(paths)
def test_change_tier_is_the_highest_single_path_tier(policy, chosen):
    whole = classify(chosen, policy).tier
    singles = max((classify([p], policy).tier for p in chosen), key=RANK.get)
    assert whole == singles


@settings(max_examples=100, deadline=None)
@given(paths)
def test_rule_order_in_the_map_does_not_matter(policy, chosen):
    from dataclasses import replace

    reversed_map = replace(policy, path_map=tuple(reversed(policy.path_map)))
    assert classify(chosen, policy).tier == classify(chosen, reversed_map).tier


@settings(max_examples=200, deadline=None)
@given(st.text(alphabet="abcXYZ./\\-_ ", min_size=1, max_size=30))
def test_any_string_gets_a_tier_and_never_crashes(policy, text):
    try:
        decision = classify([text], policy)
    except TierCheckFailed:
        return
    assert decision.tier in ("A", "B", "C")


def test_parse_name_status_counts_both_sides_of_a_rename():
    text = "M\tdocs/a.md\nR100\tdocs/old.md\tservices/decision_income/new.py\nD\tx/gone.py\n"
    assert parse_name_status(text) == [
        "docs/a.md", "docs/old.md", "services/decision_income/new.py", "x/gone.py"]


def test_retro_over_a_real_history(policy, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.test")
    git("config", "user.name", "t")
    for rel, msg in [
        ("docs/a.md", "docs"),
        ("harness/cli.py", "feature"),
        ("services/decision_income/calc.py", "rule change"),
        ("legacy/batch/job.py", "old code nobody mapped"),
        ("legacy/batch/job2.py", "more old code"),
    ]:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(msg)
        git("add", ".")
        git("commit", "-q", "-m", msg)

    report = retro(repo, policy)
    assert report["changes"] == 5
    assert report["counts"] == {"A": 1, "B": 1, "C": 3}
    assert report["tier_c_by_default"] == 2
    assert report["map_next"][0] == ("legacy/batch", 2)
    assert report["share_percent"]["C"] == 60.0
