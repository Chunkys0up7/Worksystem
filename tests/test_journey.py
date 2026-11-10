"""Stage 8 (SPEC-0006): the journey, and the discovery slice it starts from."""

from __future__ import annotations

import pytest

from conformance.journey import NOT_LIVE, render, run_journey
from conformance.scenario import load_scenario, prepare
from harness.knowledge import load_knowledge, slice_tier, validate
from harness.policy import load_policy

STAGES = ["discover", "decide", "specify", "build", "prove", "release", "run", "observe",
          "reproduce", "fix", "change", "account"]


@pytest.fixture(scope="module")
def journey(root, keydir, tmp_path_factory):
    return run_journey(root, keydir, tmp_path_factory.mktemp("journey"))


def test_journey_runs_and_the_ledger_is_whole(journey):
    """J1."""
    assert [s.stage for s in journey.steps] == STAGES
    ledger = journey.env.ledger
    assert ledger.verify() == [] and ledger.reconcile() == []
    assert journey.totals["intact"] and journey.totals["reconciled"]
    assert journey.totals["entries"] == len(ledger.entries()) > 120


def test_every_stage_lands_on_the_ledger(journey):
    """J2: no step is narration. 'specify' is the exception it names: a spec is approved in
    git, and its first ledger entry is the work order that cites it."""
    for step in journey.steps:
        if step.stage == "specify":
            continue
        assert step.entries, f"{step.stage} wrote nothing"
    cited = [e for e in journey.env.ledger.entries() if "SPEC-0002" in e.refs]
    assert cited and cited[0].fact_type == "change.proposed"
    text = render(journey)
    for needed in ("slice.decided", "skill.published", "handback.result", "release.manifest",
                   "decision.made", "case.halted", "defect.opened", "reproduction.result",
                   "review.resolved"):
        assert needed in text, needed


def test_two_executors_two_releases(journey):
    """J4."""
    assert journey.totals["executors"] == ["loop", "cli"]
    assert journey.totals["releases"] == ["decision-income@r1", "decision-income@r2"]
    manifests = journey.env.ledger.find(fact_type="release.manifest")
    assert [m.payload["executor"]["id"] for m in manifests] == ["loop", "cli"]
    assert [m.payload["skills"][0]["version"] for m in manifests] == ["1.1.0", "1.1.1"]
    assert [m.payload["spec"] for m in manifests] == ["SPEC-0002", "SPEC-0010"]


def test_the_story_the_journey_tells_is_what_happened(journey):
    by_stage = {s.stage: " ".join(s.lines) for s in journey.steps}
    assert "approver-ana launched this work and cannot approve it" in by_stage["release"]
    assert "SYN-008 used RULE-0002" in by_stage["run"]
    assert "waiting-human" in by_stage["run"] and "pass, 8 cases" in by_stage["run"]
    assert "halted" in by_stage["observe"] and "ACCEPTED an infinite amount" in by_stage["observe"]
    assert "Reproduce before fixing" in by_stage["reproduce"]
    assert "CHK-income-calculation-006 fails" in by_stage["reproduce"]
    assert "6 of 6 pass" in by_stage["fix"]
    assert "decided by decision-income@r1" in by_stage["change"]
    assert "halted on r2" in by_stage["change"]
    assert "released as decision-income@r1" in by_stage["account"]
    ledger = journey.env.ledger
    receipts = {e.payload["subject"]: e.payload["version"] for e in ledger.find(fact_type="decision.made")}
    assert receipts["SYN-004"] == "r1" and receipts["SYN-008"] == "r1"
    assert "SYN-010-after-fix" not in receipts
    assert len(ledger.find(fact_type="release.refused")) == 1


def test_the_journey_says_what_is_not_live(journey):
    """J5."""
    text = render(journey)
    assert text.count(NOT_LIVE) == 2 and "Live models: 0" in text
    assert journey.totals["live_models"] == 0
    assert "SCRIPTED executor, no live model ran" in text
    assert all(m.payload["live"] is False
               for m in journey.env.ledger.find(fact_type="release.manifest"))
    for gap in ("operating-system sandbox", "key custody", "object-locked anchor", "Tier A auto-merge"):
        assert gap in text


def test_roles_in_the_journey(journey):
    """J6."""
    policy, ledger = journey.env.policy, journey.env.ledger
    assert journey.totals["misplaced"] == 0
    assert all(policy.may_write(e.actor, e.fact_type) for e in ledger.entries())
    roles = journey.totals["roles"]
    assert len(roles) == 16 and journey.totals["identities"] >= 19
    for role in ("product-owner", "skill-publisher", "executor", "tiering", "gates", "reviewer",
                 "gateway", "approver", "release", "runtime", "decision-service",
                 "runtime-agent", "case-reviewer", "evals", "triage", "reconciler"):
        assert role in roles, role
    executors = {e.actor for e in ledger.entries() if policy.identities[e.actor].role == "executor"}
    assert executors == {"executor-loop", "executor-cli"}
    for e in ledger.entries():
        if policy.identities[e.actor].role == "executor":
            assert e.fact_type in ("change.proposed", "executor.evidence")


def test_no_amount_is_on_the_ledger(journey):
    import json

    text = json.dumps([e.payload for e in journey.env.ledger.entries()])
    for value in ("6500.00", "7250.00", "12000.00", "NaN", "Infinity"):
        assert value not in text, value


# ------------------------------------------------------------------ the slice

def test_slice_records_what_makes_it_regulated(knowledge, policy):
    """J3."""
    item = knowledge.slices["SLICE-0001"]
    assert item["decision"] == "go" and item["outcome_metric"]
    assert slice_tier(item, knowledge, policy) == "C"
    assert slice_tier({"moments": [], "data_classes": ["none"]}, knowledge, policy) == "A"
    assert slice_tier({"moments": [], "data_classes": ["internal"]}, knowledge, policy) == "B"
    assert slice_tier({"moments": ["MOM-documents-received"], "data_classes": []}, knowledge, policy) == "A"
    assert slice_tier({"moments": ["MOM-income-calculated"], "data_classes": []}, knowledge, policy) == "C"


@pytest.mark.parametrize("old, new, expect", [
    ("obligations: [OBL-0001]", "obligations: []", "carries OBL-0001, which the slice does not record"),
    ("data_classes: [consumer-personal, credit-decision]", "data_classes: [consumer-personal]",
     "handles credit-decision, which the slice does not record"),
    ("decision: go", "decision: probably", "decision must be go, clarify or kill"),
    ("outcome_metric: share", "outcome_note: share", "states no outcome metric"),
    ("moments: [MOM-income-calculated, MOM-conflict-review]", "moments: [MOM-elsewhere]",
     "moment MOM-elsewhere is on no blueprint"),
    ("decisions: [PD-0001, PD-0002]", "decisions: [PD-0404]", "PD-0404 is not in the register"),
    ("id: SLICE-0001", "id: slice-one", "does not match file name"),
])
def test_slice_broken_links_fail_validation(root, tmp_path, old, new, expect):
    """J3."""
    repo = prepare(load_scenario(root, "pd-0002"), root, tmp_path / "repo")
    path = repo / "knowledge/slices/SLICE-0001.yaml"
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, new, 1))
    problems = validate(load_knowledge(repo), load_policy(repo))
    assert any(expect in p for p in problems), problems
