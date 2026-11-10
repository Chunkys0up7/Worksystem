"""The ledger API. The only way evidence gets written, and it fails closed.

Three layers protect an entry, and each catches what the one before misses:

1. The writer's signature over the content. Editing a payload breaks it unless
   the attacker also holds that writer's key.
2. The hash chain. Recomputing one entry's hash breaks the link from the next.
3. The anchor: the head of the chain held somewhere the database owner cannot
   write. Rewriting or truncating the tail of the chain disagrees with it.

Append rejects, and never drops silently. A caller that cannot record evidence
must block (see harness/gates.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from .. import identity
from ..canonical import hash_obj
from ..policy import Policy

GENESIS = "0" * 64


class LedgerRejected(Exception):
    """The submission broke a rule. `code` says which one."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class LedgerUnavailable(Exception):
    """The store could not be read or written. Callers must block."""


@dataclass(frozen=True)
class Submission:
    actor: str
    fact_type: str
    payload: dict[str, Any]
    refs: tuple[str, ...]
    signature: str


def content_hash(
    actor: str, fact_type: str, payload: dict[str, Any], refs: tuple[str, ...]
) -> str:
    return hash_obj(
        {"actor": actor, "fact_type": fact_type, "payload": payload, "refs": list(refs)}
    )


def sign_submission(
    keydir: Path,
    actor: str,
    fact_type: str,
    payload: dict[str, Any],
    refs: tuple[str, ...] | list[str] = (),
    *,
    key_of: str | None = None,
) -> Submission:
    """Build a signed submission.

    `key_of` signs with another identity's key while claiming `actor`. It exists
    for the attack pack, to show that a claimed identity is not enough.
    """
    refs = tuple(refs)
    digest = content_hash(actor, fact_type, payload, refs)
    signature = identity.sign(keydir, key_of or actor, digest.encode())
    return Submission(actor, fact_type, payload, refs, signature)


@dataclass(frozen=True)
class Entry:
    seq: int
    ts: str
    actor: str
    fact_type: str
    record_class: str
    retain_until: str
    dispose_after: str
    payload: dict[str, Any]
    refs: tuple[str, ...]
    content_hash: str
    signature: str
    prev_hash: str
    entry_hash: str

    @property
    def id(self) -> str:
        return f"LED-{self.seq}"

    def body(self) -> dict[str, Any]:
        """Everything the entry hash covers."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "actor": self.actor,
            "fact_type": self.fact_type,
            "record_class": self.record_class,
            "retain_until": self.retain_until,
            "dispose_after": self.dispose_after,
            "payload": self.payload,
            "refs": list(self.refs),
            "content_hash": self.content_hash,
            "signature": self.signature,
            "prev_hash": self.prev_hash,
        }


class Store(Protocol):
    def init(self) -> None: ...
    def head(self) -> tuple[int, str] | None: ...
    def append(self, entry: Entry) -> None: ...
    def rows(self) -> Iterator[Entry]: ...


class FileAnchor:
    """Holds the head of the chain outside the database.

    A file stands in for object-locked storage written under its own identity.
    The property that matters is that whoever can write the database cannot
    write this. A file on the same disk does not have that property; the
    interface does. See docs/limits.md.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> tuple[int, str] | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text())
        return int(data["seq"]), str(data["entry_hash"])

    def write(self, seq: int, entry_hash: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seq": seq, "entry_hash": entry_hash}))
        tmp.replace(self.path)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def find_personal_data(payload: Any, denied: frozenset[str], path: str = "") -> str | None:
    """Return the path of the first denied key, or None.

    Personal data goes in by reference only: a key ending in `_ref` holding a
    pointer into an erasable store. This is a key lint, a floor and not a
    classifier. See docs/limits.md.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key).lower() in denied:
                return here
            hit = find_personal_data(value, denied, here)
            if hit:
                return hit
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            hit = find_personal_data(value, denied, f"{path}[{index}]")
            if hit:
                return hit
    return None


class Ledger:
    def __init__(
        self,
        store: Store,
        policy: Policy,
        anchor: FileAnchor,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.store = store
        self.policy = policy
        self.anchor = anchor
        self.clock = clock

    # ---------------------------------------------------------------- write

    def append(self, sub: Submission) -> Entry:
        policy = self.policy

        ident = policy.identities.get(sub.actor)
        if ident is None:
            raise LedgerRejected("unknown-identity", sub.actor)

        record_class = policy.record_class_of(sub.fact_type)
        if record_class is None:
            raise LedgerRejected("unknown-fact-type", sub.fact_type)

        writer = policy.writer_of(sub.fact_type)
        if writer != sub.actor:
            raise LedgerRejected(
                "not-authoritative-writer",
                f"{sub.fact_type} is written only by {writer}, not {sub.actor}",
            )

        digest = content_hash(sub.actor, sub.fact_type, sub.payload, sub.refs)
        if not identity.verify(ident.public_key, digest.encode(), sub.signature):
            raise LedgerRejected(
                "bad-signature", f"signature does not verify for {sub.actor}"
            )

        hit = find_personal_data(sub.payload, policy.personal_data_keys)
        if hit:
            raise LedgerRejected(
                "personal-data-in-payload",
                f"{hit}: personal data is recorded by reference only",
            )

        try:
            head = self.store.head()
        except Exception as exc:  # any store failure blocks
            raise LedgerUnavailable(f"cannot read ledger head: {exc}") from exc

        try:
            anchored = self.anchor.read()
        except Exception as exc:
            raise LedgerUnavailable(f"cannot read anchor: {exc}") from exc
        if head != anchored:
            raise LedgerRejected(
                "chain-head-mismatch",
                f"store head {head} does not match anchor {anchored}",
            )

        seq = head[0] + 1 if head else 1
        prev_hash = head[1] if head else GENESIS
        now = self.clock()
        partial = Entry(
            seq=seq,
            ts=now.isoformat(),
            actor=sub.actor,
            fact_type=sub.fact_type,
            record_class=record_class.name,
            retain_until=(now + timedelta(days=record_class.floor_days)).date().isoformat(),
            dispose_after=(now + timedelta(days=record_class.ceiling_days)).date().isoformat(),
            payload=sub.payload,
            refs=sub.refs,
            content_hash=digest,
            signature=sub.signature,
            prev_hash=prev_hash,
            entry_hash="",
        )
        entry = Entry(**{**partial.__dict__, "entry_hash": hash_obj(partial.body())})

        try:
            self.store.append(entry)
        except Exception as exc:
            raise LedgerUnavailable(f"cannot write ledger entry: {exc}") from exc
        try:
            self.anchor.write(entry.seq, entry.entry_hash)
        except Exception as exc:
            # The entry is stored but not anchored. The next append will see the
            # mismatch and refuse, so the gap cannot pass unnoticed.
            raise LedgerUnavailable(f"entry stored but anchor failed: {exc}") from exc
        return entry

    # ----------------------------------------------------------------- read

    def entries(self) -> list[Entry]:
        return list(self.store.rows())

    def find(self, *, fact_type: str | None = None, ref: str | None = None) -> list[Entry]:
        out = []
        for entry in self.store.rows():
            if fact_type and entry.fact_type != fact_type:
                continue
            if ref and ref not in entry.refs:
                continue
            out.append(entry)
        return out

    def verify(self) -> list[str]:
        """Re-derive the whole chain. Returns problems found; empty means intact."""
        problems: list[str] = []
        prev_hash = GENESIS
        expected_seq = 1
        last: Entry | None = None

        for entry in self.store.rows():
            if entry.seq != expected_seq:
                problems.append(
                    f"sequence gap: expected LED-{expected_seq}, found {entry.id}"
                )
            if hash_obj(entry.body()) != entry.entry_hash:
                problems.append(f"{entry.id}: entry hash does not match its contents")
            if entry.prev_hash != prev_hash:
                problems.append(f"{entry.id}: link to the previous entry is broken")

            digest = content_hash(entry.actor, entry.fact_type, entry.payload, entry.refs)
            ident = self.policy.identities.get(entry.actor)
            if digest != entry.content_hash:
                problems.append(f"{entry.id}: content hash does not match its payload")
            if ident is None or not identity.verify(
                ident.public_key, digest.encode(), entry.signature
            ):
                problems.append(
                    f"{entry.id}: writer signature does not verify for {entry.actor}"
                )
            if self.policy.writer_of(entry.fact_type) != entry.actor:
                problems.append(
                    f"{entry.id}: {entry.actor} is not the writer of {entry.fact_type}"
                )

            prev_hash = entry.entry_hash
            expected_seq = entry.seq + 1
            last = entry

        head = (last.seq, last.entry_hash) if last else None
        anchored = self.anchor.read()
        if head != anchored:
            if anchored and (head is None or head[0] < anchored[0]):
                problems.append(
                    f"anchor is at LED-{anchored[0]} but the store ends at "
                    f"{'LED-' + str(head[0]) if head else 'nothing'}: entries removed"
                )
            else:
                problems.append(
                    f"store head {head} does not match anchor {anchored}"
                )
        return problems

    # ------------------------------------------------------- completeness

    def reconcile(self) -> list[str]:
        """Completeness: every entry of one type has its required companions.

        Rules come from policy/records-schedule.yaml. A missing companion is a
        finding; it is the reconciler's job to report it, not to repair it.
        """
        entries = self.entries()
        missing: list[str] = []
        for rule in self.policy.completeness:
            source, required = rule["for_each"], rule["require"]
            have: dict[str, set[str]] = {}
            for entry in entries:
                subject = entry.payload.get("subject")
                if subject:
                    have.setdefault(entry.fact_type, set()).add(subject)
            for subject in sorted(have.get(source, set())):
                for fact_type in required:
                    if subject not in have.get(fact_type, set()):
                        missing.append(f"{subject}: has {source} but no {fact_type}")
        return missing
