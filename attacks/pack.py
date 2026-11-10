"""The attack pack: scripted attempts to break the build's own claims.

Each attack runs in a throwaway copy of the repository, makes one attempt, and
reports whether the control HELD. Two rows are marked LIMIT: they show an
attack this build does not stop, and name the control that would. Those rows
are here on purpose. A demo that only shows what works proves little.

Run:  python -m harness attacks
Add an attack: write a function, decorate it with @attack, return a Result.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness.canonical import hash_obj  # noqa: E402
from harness.change import run_change  # noqa: E402
from harness.gates import record_or_block  # noqa: E402
from harness.knowledge import SkillInvalid, load_knowledge, publish, validate  # noqa: E402
from harness.ledger import (  # noqa: E402
    Entry,
    FileAnchor,
    Ledger,
    LedgerRejected,
    sign_submission,
)
from harness.ledger.store import COLUMNS, SqliteStore, _to_row  # noqa: E402
from harness.policy import PolicyError, load_policy  # noqa: E402
from harness.tiering import TierCheckFailed, classify  # noqa: E402

COPIED = ("policy", "knowledge", "services", "demo-keys")


@dataclass
class Result:
    held: bool | None  # True held, False broken, None a stated limit
    observed: str


@dataclass
class Attack:
    name: str
    claim: str
    attempt: str
    fn: Callable[["Sandbox"], Result]


ATTACKS: list[Attack] = []


def attack(name: str, claim: str, attempt: str):
    def register(fn):
        ATTACKS.append(Attack(name, claim, attempt, fn))
        return fn

    return register


class Sandbox:
    """A private copy of the repository with its own empty ledger."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="attack-")
        self.root = Path(self._tmp.name)
        for name in COPIED:
            shutil.copytree(REPO / name, self.root / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        self.keydir = self.root / "demo-keys"
        self.db = self.root / ".ledger" / "ledger.db"
        self.anchor_path = self.root / ".ledger" / "anchor.json"
        self.reload()
        self.store.init()

    def reload(self) -> None:
        self.policy = load_policy(self.root)
        self.knowledge = load_knowledge(self.root)
        self.store = SqliteStore(self.db)
        self.ledger = Ledger(self.store, self.policy, FileAnchor(self.anchor_path))

    def change(self, change_id: str, paths: list[str]):
        return run_change(change_id, paths, ledger=self.ledger, keydir=self.keydir,
                          policy=self.policy, knowledge=self.knowledge)

    def seed(self) -> None:
        """Put some real history in the ledger."""
        self.change("CHG-seed-1", ["docs/readme.md"])
        self.change("CHG-seed-2", ["services/decision_income/calc.py"])

    def raw(self) -> sqlite3.Connection:
        """Database-owner access: the triggers are gone."""
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TRIGGER IF EXISTS ledger_no_update")
        conn.execute("DROP TRIGGER IF EXISTS ledger_no_delete")
        return conn

    def close(self) -> None:
        self._tmp.cleanup()


def _rebuild_chain(sb: Sandbox, keep: list[Entry]) -> None:
    """Rewrite the whole table as a valid chain over `keep`, as a DB owner could."""
    conn = sb.raw()
    conn.execute("DELETE FROM ledger")
    prev = "0" * 64
    for seq, old in enumerate(keep, start=1):
        fields = {**old.__dict__, "seq": seq, "prev_hash": prev, "entry_hash": ""}
        partial = Entry(**fields)
        entry = Entry(**{**fields, "entry_hash": hash_obj(partial.body())})
        marks = ",".join("?" for _ in COLUMNS)
        conn.execute(f"INSERT INTO ledger ({','.join(COLUMNS)}) VALUES ({marks})", _to_row(entry))
        prev = entry.entry_hash
    conn.commit()
    conn.close()


# ----------------------------------------------------------- who may write

@attack(
    "agent sets its own tier",
    "Tier is set by deterministic rules, never by the authoring agent.",
    "The executor signs a tier.assigned entry, Tier A, for its own Tier C change.",
)
def agent_sets_own_tier(sb: Sandbox) -> Result:
    try:
        sb.ledger.append(sign_submission(
            sb.keydir, "agent-executor", "tier.assigned",
            {"subject": "CHG-x", "tier": "A"}, ["CHG-x"]))
    except LedgerRejected as exc:
        return Result(exc.code == "not-authoritative-writer", f"rejected: {exc}")
    return Result(False, "the ledger accepted a tier written by the executor")


@attack(
    "agent claims to be CI",
    "Each identity has its own signing key; a claimed identity is not enough.",
    "The executor submits a passing gate.result naming ci-gates as the writer, signed with its own key.",
)
def agent_forges_ci(sb: Sandbox) -> Result:
    try:
        sb.ledger.append(sign_submission(
            sb.keydir, "ci-gates", "gate.result",
            {"subject": "CHG-x", "outcome": "passed"}, ["CHG-x"], key_of="agent-executor"))
    except LedgerRejected as exc:
        return Result(exc.code == "bad-signature", f"rejected: {exc}")
    return Result(False, "the ledger accepted a forged CI entry")


@attack(
    "two writers for one fact",
    "One authoritative writer per fact type.",
    "Policy is edited so the executor may also write gate.result.",
)
def two_writers(sb: Sandbox) -> Result:
    path = sb.root / "policy" / "identities.yaml"
    path.write_text(path.read_text().replace(
        "may_write: [change.proposed]", "may_write: [change.proposed, gate.result]"))
    try:
        load_policy(sb.root)
    except PolicyError as exc:
        return Result(True, f"policy refused to load: {exc}")
    return Result(False, "policy loaded with two writers for gate.result")


# ----------------------------------------------------------- the evidence

@attack(
    "edit a recorded result",
    "Evidence cannot be altered after the fact without detection.",
    "A database owner drops the append-only trigger and rewrites a held gate result as passed.",
)
def edit_row(sb: Sandbox) -> Result:
    sb.seed()
    conn = sb.raw()
    conn.execute("UPDATE ledger SET payload = replace(payload, 'held-for-approval', 'passed') "
                 "WHERE fact_type = 'gate.result'")
    conn.commit()
    conn.close()
    problems = sb.ledger.verify()
    return Result(bool(problems), "; ".join(problems[:2]) or "verify found nothing")


@attack(
    "edit a result and repair its hash",
    "The hash chain links every entry to the one before.",
    "A Tier C assignment is rewritten as Tier A, and the attacker recomputes that entry's hash "
    "so it matches its new contents.",
)
def edit_row_fix_hash(sb: Sandbox) -> Result:
    sb.seed()
    entries = sb.ledger.entries()
    target = next(e for e in entries
                  if e.fact_type == "tier.assigned" and e.payload["tier"] == "C")
    forged = Entry(**{**target.__dict__, "payload": {**target.payload, "tier": "A"},
                      "entry_hash": ""})
    new_hash = hash_obj(forged.body())
    conn = sb.raw()
    conn.execute("UPDATE ledger SET payload = ?, entry_hash = ? WHERE seq = ?",
                 (_to_row(forged)[7], new_hash, target.seq))
    conn.commit()
    conn.close()
    problems = sb.ledger.verify()
    wanted = [p for p in problems if "link" in p or "signature" in p or "content hash" in p]
    return Result(bool(wanted), "; ".join(wanted[:3]) or "verify found nothing")


@attack(
    "remove a failed check and rebuild the chain",
    "The anchor holds the chain head outside the database.",
    "A database owner deletes an inconvenient entry and rewrites every later hash so the chain "
    "is valid again. No signed content is touched, so every signature still verifies.",
)
def remove_and_rebuild(sb: Sandbox) -> Result:
    sb.seed()
    entries = sb.ledger.entries()
    keep = [e for e in entries if not (e.fact_type == "check.result")]
    _rebuild_chain(sb, keep)
    problems = sb.ledger.verify()
    anchored = [p for p in problems if "anchor" in p]
    return Result(bool(anchored), "; ".join(anchored) or f"verify found: {problems}")


@attack(
    "truncate the tail",
    "A hash chain alone cannot see missing entries at the end; the anchor can.",
    "A database owner deletes the newest entries.",
)
def truncate_tail(sb: Sandbox) -> Result:
    sb.seed()
    conn = sb.raw()
    conn.execute("DELETE FROM ledger WHERE seq > 3")
    conn.commit()
    conn.close()
    problems = sb.ledger.verify()
    try:
        sb.change("CHG-after", ["docs/x.md"])
        blocked = not sb.ledger.find(ref="CHG-after")
    except Exception:
        blocked = True
    return Result(bool(problems) and blocked,
                  f"{problems[0] if problems else 'verify found nothing'}; "
                  f"new writes {'refused' if blocked else 'ACCEPTED'}")


@attack(
    "rebuild the chain and the anchor",
    "LIMIT. The anchor only works if the database owner cannot write it.",
    "The attacker owns the database and the anchor file, removes entries, rebuilds both.",
)
def rebuild_chain_and_anchor(sb: Sandbox) -> Result:
    sb.seed()
    keep = [e for e in sb.ledger.entries() if e.fact_type != "check.result"]
    _rebuild_chain(sb, keep)
    head = sb.store.head()
    FileAnchor(sb.anchor_path).write(*head)
    problems = sb.ledger.verify()
    if problems:
        return Result(True, "; ".join(problems[:2]))
    return Result(None, "NOT DETECTED here. The control is object-locked storage for the anchor, "
                        "written under a separate identity. This build uses a file. The daily "
                        "reconciliation against CI run records is the second line: it would "
                        "report the missing check results.")


@attack(
    "personal data in the ledger",
    "Personal data is recorded by reference only.",
    "CI writes a check result whose payload carries an applicant's name and income.",
)
def personal_data(sb: Sandbox) -> Result:
    try:
        sb.ledger.append(sign_submission(
            sb.keydir, "ci-gates", "check.result",
            {"subject": "CHK-x-001", "outcome": "pass",
             "sample": {"name": "A. Applicant", "income": "5000"}}, []))
    except LedgerRejected as exc:
        return Result(exc.code == "personal-data-in-payload", f"rejected: {exc}")
    return Result(False, "the ledger stored personal data")


@attack(
    "the ledger is down",
    "The ledger API fails closed: no evidence, no pass.",
    "The store is unreachable when a passing gate result is recorded.",
)
def ledger_down(sb: Sandbox) -> Result:
    broken = Ledger(SqliteStore(sb.root / "missing" / "ledger.db"), sb.policy,
                    FileAnchor(sb.anchor_path))
    outcome = record_or_block(broken, sign_submission(
        sb.keydir, "ci-gates", "gate.result",
        {"subject": "CHG-x", "outcome": "passed"}, ["CHG-x"]), passed=True)
    return Result(not outcome.passed, outcome.reason)


# ------------------------------------------------------------- the tiers

@attack(
    "hide a regulated path behind a harmless one",
    "A change takes the highest tier it touches; paths are normalised first.",
    "The change lists docs/../services/decision_income/calc.py alongside a docs file.",
)
def path_traversal(sb: Sandbox) -> Result:
    decision = classify(["docs/guide.md", "docs/../services/decision_income/calc.py"], sb.policy)
    return Result(decision.tier == "C", f"Tier {decision.tier}")


@attack(
    "slip past the map with a near-miss path",
    "A path nothing maps is Tier C until mapped.",
    "Files at Docs/notes.md, services2/calc.py and ../outside.py.",
)
def unmapped_paths(sb: Sandbox) -> Result:
    tiers = {p: classify([p], sb.policy).tier
             for p in ("Docs/notes.md", "services2/calc.py", "../outside.py")}
    return Result(all(t == "C" for t in tiers.values()), str(tiers))


@attack(
    "lower the tier by editing the rules in the same change",
    "The rulebook's own files are Tier C.",
    "The change remaps services/ to Tier A in policy/path-map.yaml and edits the service.",
)
def edit_rules_in_change(sb: Sandbox) -> Result:
    run = sb.change("CHG-sneaky", ["policy/path-map.yaml", "services/decision_income/calc.py"])
    return Result(run.tier == "C" and run.outcome != "passed", f"Tier {run.tier}, {run.outcome}")


@attack(
    "change a skill nobody has mapped",
    "A skill with unknown consuming nodes fails the tier check; it does not default down.",
    "A new skill directory is changed with no skill-map entry.",
)
def unmapped_skill(sb: Sandbox) -> Result:
    try:
        decision = classify(["knowledge/skills/new-skill/SKILL.md"], sb.policy)
    except TierCheckFailed as exc:
        return Result(True, f"tier check failed: {exc}")
    return Result(False, f"classified as Tier {decision.tier}")


# --------------------------------------------------------- the knowledge

@attack(
    "the code quietly disagrees with the rule",
    "Every rule has a paired deterministic check that CI runs itself.",
    "The executor changes the history requirement in the service from 24 months to 12 and "
    "reports success.",
)
def code_drifts_from_card(sb: Sandbox) -> Result:
    path = sb.root / "services" / "decision_income" / "calc.py"
    path.write_text(path.read_text().replace(
        "VARIABLE_HISTORY_MIN_MONTHS = 24", "VARIABLE_HISTORY_MIN_MONTHS = 12"))
    run = sb.change("CHG-drift", ["services/decision_income/calc.py"])
    failed = [s.strip() for s in run.steps if "BLOCK: check failed" in s]
    return Result(run.outcome == "blocked" and bool(failed),
                  f"{run.outcome}; {len(failed)} check(s) failed and were recorded")


@attack(
    "hide a rule change behind stale bytecode",
    "Checks run against the source as it stands, never a cached build of it.",
    "The service is edited so its size and timestamp do not change, leaving the old compiled "
    "file beside it, which an ordinary import would trust.",
)
def stale_bytecode(sb: Sandbox) -> Result:
    import os
    import py_compile

    path = sb.root / "services" / "decision_income" / "calc.py"
    stat = path.stat()
    py_compile.compile(str(path))
    path.write_text(path.read_text().replace(
        "VARIABLE_HISTORY_MIN_MONTHS = 24", "VARIABLE_HISTORY_MIN_MONTHS = 12"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    run = sb.change("CHG-stale", ["services/decision_income/calc.py"])
    failed = [s for s in run.steps if "BLOCK: check failed" in s]
    return Result(run.outcome == "blocked" and bool(failed),
                  f"{run.outcome}; {len(failed)} check(s) failed against the edited source")


@attack(
    "weaken the check along with the code",
    "Skills are signed and content-hashed; a changed skill is not the released skill.",
    "The executor edits the skill's check so the drifted code passes.",
)
def weaken_the_check(sb: Sandbox) -> Result:
    check = sb.root / "knowledge/skills/income-calculation/checks/check_income.py"
    check.write_text(check.read_text().replace(
        'assert VARIABLE_HISTORY_MIN_MONTHS == minimum, "minimum history differs from RULE-0001"',
        "pass"))
    sb.reload()
    run = sb.change("CHG-weak", ["services/decision_income/calc.py",
                                 "knowledge/skills/income-calculation/checks/check_income.py"])
    blocked = [s for s in run.steps if "content changed since release" in s]
    return Result(run.outcome == "blocked" and bool(blocked),
                  f"{run.outcome}; {blocked[0].strip() if blocked else run.steps}")


@attack(
    "weaken the check and re-sign the skill",
    "LIMIT. Signing proves who released a skill, not that the release was reviewed.",
    "The attacker also holds the skill publisher's key and re-signs the weakened skill.",
)
def weaken_and_resign(sb: Sandbox) -> Result:
    check = sb.root / "knowledge/skills/income-calculation/checks/check_income.py"
    check.write_text(check.read_text().replace(
        'assert VARIABLE_HISTORY_MIN_MONTHS == minimum, "minimum history differs from RULE-0001"',
        "pass"))
    sb.reload()
    publish(sb.knowledge.skills["SKL-income-calculation"], sb.keydir)
    sb.reload()
    run = sb.change("CHG-resign", ["knowledge/skills/income-calculation/checks/check_income.py"])
    if run.outcome == "blocked":
        return Result(True, "blocked")
    return Result(None, f"NOT STOPPED by signing: the change is Tier {run.tier} and "
                        f"{run.outcome}, so it reaches two-person review, which is the control "
                        "(stage 5). Key custody keeps the publisher key from the executor. "
                        "Neither is demonstrated in this build.")


@attack(
    "a skill that phones home",
    "A skill makes no network call and holds no credential.",
    "A network import is added to a skill check before release.",
)
def skill_with_network(sb: Sandbox) -> Result:
    check = sb.root / "knowledge/skills/income-calculation/checks/check_income.py"
    check.write_text("import urllib.request\n" + check.read_text())
    sb.reload()
    try:
        publish(sb.knowledge.skills["SKL-income-calculation"], sb.keydir)
    except SkillInvalid as exc:
        return Result(True, f"release refused: {exc}")
    return Result(False, "the skill was released with a network import")


@attack(
    "a rule with no check",
    "Missing links fail the build.",
    "A new rule card is added to the skill with no paired check.",
)
def rule_without_check(sb: Sandbox) -> Result:
    card = sb.root / "knowledge/cards/RULE-0002.yaml"
    card.write_text("id: RULE-0002\ntype: rule\nmoment: MOM-income-calculated\n"
                    "title: A new rule\nimplements: [PD-0001]\n")
    manifest = sb.root / "knowledge/skills/income-calculation/manifest.yaml"
    manifest.write_text(manifest.read_text().replace(
        "cards: [FACT-0001, RULE-0001, CALC-0001, PROC-0001]",
        "cards: [FACT-0001, RULE-0001, RULE-0002, CALC-0001, PROC-0001]"))
    sb.reload()
    problems = [p for p in validate(sb.knowledge, sb.policy) if "RULE-0002" in p]
    return Result(bool(problems), "; ".join(problems) or "validation passed")


@attack(
    "a regulated moment with no obligation",
    "The blueprint marks regulated moments and each carries its obligations.",
    "The obligations are removed from a regulated moment on the blueprint.",
)
def moment_without_obligation(sb: Sandbox) -> Result:
    path = sb.root / "knowledge/blueprint/income-verification.yaml"
    path.write_text(path.read_text().replace("    obligations: [OBL-0001]\n", "", 1))
    sb.reload()
    problems = [p for p in validate(sb.knowledge, sb.policy) if "regulated moment" in p]
    return Result(bool(problems), "; ".join(problems) or "validation passed")


# ------------------------------------------------------------------ runner

def run_all() -> list[tuple[Attack, Result]]:
    results = []
    for item in ATTACKS:
        sb = Sandbox()
        try:
            result = item.fn(sb)
        except Exception as exc:  # an attack that crashes the build has broken it
            result = Result(False, f"unexpected {type(exc).__name__}: {exc}")
        finally:
            sb.close()
        results.append((item, result))
    return results


def main() -> int:
    results = run_all()
    label = {True: "HELD ", False: "BROKEN", None: "LIMIT"}
    for index, (item, result) in enumerate(results, start=1):
        print(f"{index:>2}. [{label[result.held]}] {item.name}")
        print(f"      claim:    {item.claim}")
        print(f"      attempt:  {item.attempt}")
        print(f"      observed: {result.observed}")
    held = sum(1 for _, r in results if r.held is True)
    limits = sum(1 for _, r in results if r.held is None)
    broken = sum(1 for _, r in results if r.held is False)
    print(f"\n{held} held, {limits} stated limit(s), {broken} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
