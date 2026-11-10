"""The ID scheme. Every link in the chain is an ID that can be parsed and checked.

decision (PD, ADR, OBL) -> skill -> spec -> change -> check -> ledger entry
"""

from __future__ import annotations

import re

KINDS: dict[str, re.Pattern[str]] = {
    "product-decision": re.compile(r"^PD-\d{4}$"),
    "architecture-decision": re.compile(r"^ADR-\d{3}$"),
    "obligation": re.compile(r"^OBL-\d{4}$"),
    "moment": re.compile(r"^MOM-[a-z0-9-]+$"),
    "card": re.compile(r"^(FACT|RULE|CALC|PROC)-\d{4}$"),
    "skill": re.compile(r"^SKL-[a-z0-9-]+$"),
    "skill-version": re.compile(r"^SKL-[a-z0-9-]+@\d+\.\d+\.\d+$"),
    "node": re.compile(r"^NODE-[a-z0-9-]+$"),
    "spec": re.compile(r"^SPEC-\d{4}$"),
    "change": re.compile(r"^CHG-[A-Za-z0-9-]+$"),
    "check": re.compile(r"^CHK-[a-z0-9-]+-\d{3}$"),
    "eval": re.compile(r"^EVL-[a-z0-9-]+-\d{3}$"),
    "defect": re.compile(r"^DEF-\d{4}$"),
    "slice": re.compile(r"^SLICE-\d{4}$"),
    "ledger-entry": re.compile(r"^LED-\d+$"),
}

DECISION_KINDS = ("product-decision", "architecture-decision", "obligation")


def kind_of(identifier: str) -> str | None:
    for kind, pattern in KINDS.items():
        if pattern.match(identifier):
            return kind
    return None


def is_decision(identifier: str) -> bool:
    return kind_of(identifier) in DECISION_KINDS


def card_type(identifier: str) -> str | None:
    if kind_of(identifier) != "card":
        return None
    return identifier.split("-", 1)[0]
