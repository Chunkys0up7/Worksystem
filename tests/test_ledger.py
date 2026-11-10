"""Ledger contract. Runs against SQLite, and against Postgres when LEDGER_PG_DSN is set."""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness.gates import record_or_block
from harness.ledger import (
    GENESIS,
    FileAnchor,
    Ledger,
    LedgerRejected,
    sign_submission,
)
from harness.ledger.store import COLUMNS, SqliteStore

from .conftest import fixed_clock


def tier_sub(keydir, subject="CHG-1", tier="B", **kw):
    return sign_submission(
        keydir, "ci-tiering", "tier.assigned", {"subject": subject, "tier": tier}, [subject], **kw
    )


def test_append_builds_a_linked_chain(ledger, keydir):
    first = ledger.append(tier_sub(keydir, "CHG-1"))
    second = ledger.append(tier_sub(keydir, "CHG-2"))
    assert (first.seq, second.seq) == (1, 2)
    assert first.prev_hash == GENESIS
    assert second.prev_hash == first.entry_hash
    assert first.id == "LED-1"
    assert ledger.verify() == []
    assert [e.entry_hash for e in ledger.entries()] == [first.entry_hash, second.entry_hash]


def test_record_class_and_retention_window_are_fixed_at_write(ledger, keydir, policy):
    entry = ledger.append(tier_sub(keydir))
    rc = policy.record_classes["delivery-evidence"]
    assert entry.record_class == "delivery-evidence"
    assert entry.ts == "2026-11-10T12:00:00+00:00"
    assert entry.retain_until < entry.dispose_after
    from datetime import date, timedelta

    assert entry.retain_until == (date(2026, 11, 10) + timedelta(days=rc.floor_days)).isoformat()
    assert entry.dispose_after == (date(2026, 11, 10) + timedelta(days=rc.ceiling_days)).isoformat()


@pytest.mark.parametrize(
    "build, code",
    [
        (lambda k: sign_submission(k, "agent-executor", "tier.assigned", {"subject": "CHG-1"}),
         "not-authoritative-writer"),
        (lambda k: sign_submission(k, "ci-tiering", "tier.assigned", {"subject": "CHG-1"},
                                   key_of="agent-executor"), "bad-signature"),
        (lambda k: sign_submission(k, "ci-tiering", "no.such.fact", {"subject": "CHG-1"}),
         "unknown-fact-type"),
        (lambda k: sign_submission(k, "ci-gates", "check.result",
                                   {"subject": "CHK-a-001", "detail": {"SSN": "x"}}),
         "personal-data-in-payload"),
        (lambda k: sign_submission(k, "ci-gates", "check.result",
                                   {"subject": "CHK-a-001", "rows": [{"email": "x"}]}),
         "personal-data-in-payload"),
    ],
)
def test_rejections(ledger, keydir, build, code):
    with pytest.raises(LedgerRejected) as caught:
        ledger.append(build(keydir))
    assert caught.value.code == code
    assert ledger.entries() == []


def test_unknown_identity_is_rejected(ledger, keydir):
    sub = tier_sub(keydir)
    forged = type(sub)("nobody", sub.fact_type, sub.payload, sub.refs, sub.signature)
    with pytest.raises(LedgerRejected) as caught:
        ledger.append(forged)
    assert caught.value.code == "unknown-identity"


def test_signature_covers_payload_and_refs(ledger, keydir):
    sub = tier_sub(keydir, tier="C")
    for altered in (
        type(sub)(sub.actor, sub.fact_type, {**sub.payload, "tier": "A"}, sub.refs, sub.signature),
        type(sub)(sub.actor, sub.fact_type, sub.payload, ("CHG-other",), sub.signature),
    ):
        with pytest.raises(LedgerRejected) as caught:
            ledger.append(altered)
        assert caught.value.code == "bad-signature"


def test_reference_keys_are_allowed(ledger, keydir):
    entry = ledger.append(
        sign_submission(keydir, "ci-gates", "check.result",
                        {"subject": "CHK-a-001", "applicant_ref": "vault://case/123"})
    )
    assert entry.payload["applicant_ref"] == "vault://case/123"


def test_database_refuses_update_and_delete(ledger, store, keydir):
    ledger.append(tier_sub(keydir))
    with pytest.raises(Exception, match="append-only"):
        with store._connect() as conn:
            conn.execute(f"UPDATE {getattr(store, 'table', 'ledger')} SET actor = 'x'")
    with pytest.raises(Exception, match="append-only"):
        with store._connect() as conn:
            conn.execute(f"DELETE FROM {getattr(store, 'table', 'ledger')}")
    assert ledger.verify() == []


def test_anchor_mismatch_blocks_writes(ledger, keydir, tmp_path):
    ledger.append(tier_sub(keydir, "CHG-1"))
    ledger.anchor.write(1, "f" * 64)
    with pytest.raises(LedgerRejected) as caught:
        ledger.append(tier_sub(keydir, "CHG-2"))
    assert caught.value.code == "chain-head-mismatch"
    assert any("anchor" in p for p in ledger.verify())


def test_missing_anchor_with_entries_is_a_mismatch(ledger, keydir):
    ledger.append(tier_sub(keydir, "CHG-1"))
    ledger.anchor.path.unlink()
    with pytest.raises(LedgerRejected):
        ledger.append(tier_sub(keydir, "CHG-2"))


def test_gate_blocks_when_store_is_down(policy, keydir, tmp_path):
    broken = Ledger(SqliteStore(tmp_path / "nowhere" / "ledger.db"), policy,
                    FileAnchor(tmp_path / "anchor.json"))
    outcome = record_or_block(broken, tier_sub(keydir), passed=True)
    assert outcome.passed is False and outcome.entry is None
    assert outcome.reason.startswith("BLOCK")


def test_gate_blocks_when_evidence_is_rejected(ledger, keydir):
    bad = sign_submission(keydir, "agent-executor", "gate.result", {"subject": "CHG-1"})
    outcome = record_or_block(ledger, bad, passed=True)
    assert outcome.passed is False and "not-authoritative-writer" in outcome.reason


def test_gate_records_a_failure_and_still_blocks(ledger, keydir):
    sub = sign_submission(keydir, "ci-gates", "gate.result", {"subject": "CHG-1", "outcome": "blocked"})
    outcome = record_or_block(ledger, sub, passed=False)
    assert outcome.passed is False and outcome.entry is not None


def test_reconcile_reports_missing_companions(ledger, keydir):
    ledger.append(sign_submission(keydir, "agent-executor", "change.proposed", {"subject": "CHG-1"}))
    assert ledger.reconcile() == ["CHG-1: has change.proposed but no gate.result"]
    ledger.append(tier_sub(keydir, "CHG-1"))
    assert ledger.reconcile() == ["CHG-1: has change.proposed but no gate.result",
                                  "CHG-1: has tier.assigned but no gate.result"]
    ledger.append(sign_submission(keydir, "ci-gates", "gate.result", {"subject": "CHG-1"}))
    assert ledger.reconcile() == []


def test_find_by_type_and_ref(ledger, keydir):
    ledger.append(tier_sub(keydir, "CHG-1"))
    ledger.append(tier_sub(keydir, "CHG-2"))
    assert [e.payload["subject"] for e in ledger.find(ref="CHG-2")] == ["CHG-2"]
    assert len(ledger.find(fact_type="tier.assigned")) == 2
    assert ledger.find(fact_type="gate.result") == []


MUTABLE = [c for c in COLUMNS if c != "seq"]


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(count=st.integers(1, 6), data=st.data())
def test_any_single_field_edit_is_detected(policy, keydir, tmp_path_factory, count, data):
    """Property: change any one stored field of any entry and verify reports it."""
    tmp = tmp_path_factory.mktemp("prop")
    store = SqliteStore(tmp / "ledger.db")
    store.init()
    ledger = Ledger(store, policy, FileAnchor(tmp / "anchor.json"), clock=fixed_clock)
    for i in range(count):
        ledger.append(tier_sub(keydir, f"CHG-{i}", tier=data.draw(st.sampled_from("ABC"))))
    assert ledger.verify() == []

    seq = data.draw(st.integers(1, count))
    column = data.draw(st.sampled_from(MUTABLE))
    conn = sqlite3.connect(store.path)
    conn.execute("DROP TRIGGER ledger_no_update")
    current = conn.execute(f"SELECT {column} FROM ledger WHERE seq = ?", (seq,)).fetchone()[0]
    if column == "payload":
        changed = current.replace('"tier"', '"tierx"')
    elif column == "refs":
        changed = '["CHG-forged"]'
    elif column == "entry_hash":
        changed = "e" * 64
    else:
        changed = current[:-1] + ("0" if current[-1] != "0" else "1")
    conn.execute(f"UPDATE ledger SET {column} = ? WHERE seq = ?", (changed, seq))
    conn.commit()
    conn.close()

    assert ledger.verify() != []
