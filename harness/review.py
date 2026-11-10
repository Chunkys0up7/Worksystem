"""Advisory review. A model that did not write the change comments on it.

Two rules, both enforced here and neither by the reviewer:

- The author never judges its own work: a reviewer from the same model family
  as the author is refused.
- Advice is advice. It is recorded under the reviewer identity and nothing in
  the gate reads it. A reviewer saying "approve" cannot pass a change and one
  saying "reject" cannot block it. Deterministic gates block; AI review advises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical import sha256_hex
from .gateway import Gateway, GatewayRefused
from .ledger import Ledger, LedgerRejected, LedgerUnavailable, sign_submission

REVIEWER_IDENTITY = "advisory-reviewer"
MAX_ADVICE = 600


def advise(
    *,
    change_id: str,
    spec_id: str,
    changed: dict[str, str],
    author_families: set[str],
    reviewer_model: str,
    gateway: Gateway,
    ledger: Ledger,
    keydir: Path,
) -> str:
    """Record advice and return a one-line account of what happened."""
    family = gateway.family_of(reviewer_model)
    if family is not None and family in author_families:
        return (f"review refused: {reviewer_model} is from the author's model family "
                f"({family}); the author does not judge its own work")
    try:
        session = gateway.session(reviewer_model, "reviewer")
        reply = session.complete([
            {"role": "system", "content": "Review this change. You advise; you do not decide."},
            {"role": "user", "content": {"change": change_id, "spec": spec_id, "files": changed}},
        ])
    except GatewayRefused as exc:
        return f"review not run: {exc}"
    text = str(reply.get("text", ""))[:MAX_ADVICE]
    payload: dict[str, Any] = {
        "subject": change_id, "spec": spec_id, "reviewer_model": reviewer_model,
        "reviewer_kind": session.usage.kind, "advice": text,
        "advice_hash": sha256_hex(text.encode()), "binding": False,
    }
    try:
        entry = ledger.append(sign_submission(
            keydir, REVIEWER_IDENTITY, "review.advice", payload, [change_id, spec_id]))
    except (LedgerRejected, LedgerUnavailable) as exc:
        return f"review advice could not be recorded ({exc}); the gate does not depend on it"
    return f"{entry.id} review.advice by {reviewer_model} (advice only): {text[:80]}"
