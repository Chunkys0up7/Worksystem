"""Gates block; nothing else does. A gate that cannot record its evidence blocks.

"No number, no merge": a gate result exists only as a ledger entry written by
the CI identity. If the write is rejected or the store is down, the answer is
BLOCK, never "pass, evidence to follow".
"""

from __future__ import annotations

from dataclasses import dataclass

from .ledger import Entry, Ledger, LedgerRejected, LedgerUnavailable, Submission


@dataclass(frozen=True)
class GateOutcome:
    passed: bool
    reason: str
    entry: Entry | None = None


def record_or_block(ledger: Ledger, submission: Submission, passed: bool) -> GateOutcome:
    """Write the evidence first. Only a recorded pass is a pass."""
    try:
        entry = ledger.append(submission)
    except LedgerRejected as exc:
        return GateOutcome(False, f"BLOCK: evidence rejected ({exc.code})")
    except LedgerUnavailable as exc:
        return GateOutcome(False, f"BLOCK: evidence could not be recorded ({exc})")
    if not passed:
        return GateOutcome(False, f"BLOCK: check failed, recorded as {entry.id}", entry)
    return GateOutcome(True, f"PASS: recorded as {entry.id}", entry)
