"""The case engine: a small graph runner with the platform's rules built in.

A graph is a set of nodes. Each node declares what it reads, what it writes and
under which identity it acts. The engine enforces the declarations:

- A node that writes a field it did not declare is stopped and the case halts.
- A regulated field can be written by one named node only.
- Every step is a checkpoint, written in the SAME database transaction as the
  ledger entries for that step. Either both exist or neither does.
- The case resolves its decision service by the version pinned at start.

No orchestration product is used. LangGraph, Temporal or an existing workflow
engine could sit here behind the same node contract; the rules above are the
part that must not depend on which.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from harness.canonical import hash_obj
from harness.gateway import Gateway
from harness.ledger import Ledger, sign_submission
from harness.policy import Policy
from harness.release import verify_stored

from .vault import Vault

ORCHESTRATOR = "rt-orchestrator"
STEP_FACTS = ("case.started", "node.completed", "case.halted", "case.migrated")
TABLES = (
    "CREATE TABLE IF NOT EXISTS runtime_cases ("
    "case_id TEXT PRIMARY KEY, graph TEXT NOT NULL, pins TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS runtime_checkpoints ("
    "case_id TEXT NOT NULL, step INTEGER NOT NULL, node TEXT NOT NULL, "
    "state TEXT NOT NULL, PRIMARY KEY (case_id, step))",
)


class ContractViolation(Exception):
    """A node did something its contract does not allow."""


class RuntimeRefused(Exception):
    pass


@dataclass
class NodeResult:
    updates: dict[str, Any] = field(default_factory=dict)
    facts: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # identity, type, payload
    next: str | None = None
    status: str = "running"


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # deterministic | agent | human
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    fn: Callable[["Context", dict[str, Any]], NodeResult] | None = None
    acts_as: tuple[str, ...] = ()  # identities it may sign as, besides the orchestrator
    after: str | None = None       # for a human node: where the case goes once resolved


@dataclass(frozen=True)
class Graph:
    id: str
    start: str
    nodes: dict[str, Node]
    regulated: dict[str, str]  # field -> the only node that may write it


@dataclass
class Context:
    case_id: str
    pins: dict[str, Any]
    vault: Vault
    releases_dir: Path
    gateway: Gateway | None
    policy: Policy
    ledger: Ledger

    def decision_service(self, unit: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Call the PINNED version of a decision service. Never the latest."""
        version = self.pins[unit]
        problems = verify_stored(self.releases_dir, unit, version, self.ledger)
        if problems:
            raise RuntimeRefused(f"{unit}@{version} does not match its sealed manifest: {problems[0]}")
        home = str(Path(__file__).resolve().parent.parent)
        with tempfile.TemporaryDirectory(prefix="svc-pycache-") as cache:
            proc = subprocess.run(
                [sys.executable, "-m", "runtime.service_runner",
                 str(self.releases_dir / unit / version)],
                input=json.dumps(inputs), capture_output=True, text=True, timeout=60, cwd=home,
                env={**os.environ, "PYTHONPATH": home, "PYTHONPYCACHEPREFIX": cache})
        if proc.returncode != 0:
            raise RuntimeRefused(f"{unit}@{version} failed: {proc.stderr.strip()[-300:]}")
        return json.loads(proc.stdout)


class Engine:
    def __init__(self, graph: Graph, *, ledger: Ledger, keydir: Path, policy: Policy,
                 vault: Vault, releases_dir: Path, gateway: Gateway | None = None,
                 crash_before_commit: Callable[[str, int], bool] | None = None) -> None:
        self.graph, self.ledger, self.keydir, self.policy = graph, ledger, keydir, policy
        self.vault, self.releases_dir, self.gateway = vault, releases_dir, gateway
        self.crash_before_commit = crash_before_commit
        with ledger.transaction() as tx:
            for statement in TABLES:
                tx.execute(statement)

    # ------------------------------------------------------------------ pins

    def channel_head(self, unit: str) -> dict[str, Any]:
        path = self.releases_dir / unit / "channel.json"
        if not path.exists():
            raise RuntimeRefused(f"{unit} has never been released")
        return json.loads(path.read_text())

    def pins(self, case_id: str) -> dict[str, Any]:
        rows = self.ledger.query("SELECT pins FROM runtime_cases WHERE case_id = ?", (case_id,))
        if not rows:
            raise RuntimeRefused(f"no such case: {case_id}")
        return json.loads(rows[0][0])

    # ----------------------------------------------------------------- state

    def state(self, case_id: str, verify: bool = True) -> dict[str, Any]:
        """The latest checkpoint, checked against the ledger entry that recorded it."""
        rows = self.ledger.query(
            "SELECT state FROM runtime_checkpoints WHERE case_id = ? ORDER BY step DESC LIMIT 1",
            (case_id,))
        if not rows:
            raise RuntimeRefused(f"no such case: {case_id}")
        state = json.loads(rows[0][0])
        if verify:
            recorded = [e.payload.get("state_hash") for e in self.ledger.find(ref=case_id)
                        if e.fact_type in STEP_FACTS and e.payload.get("step") == state.get("step")]
            if hash_obj(state) not in recorded:
                raise RuntimeRefused(
                    f"case {case_id}: the checkpoint at step {state.get('step')} does not match "
                    "the ledger; it was altered, or its ledger entry is missing")
        return state

    def _commit(self, case_id: str, state: dict[str, Any], node: str,
                facts: list[tuple[str, str, dict[str, Any]]], extra_sql=()) -> None:
        """The checkpoint and its ledger entries, in one transaction."""
        with self.ledger.transaction() as tx:
            for sql, params in extra_sql:
                tx.execute(sql, params)
            tx.execute(
                "INSERT INTO runtime_checkpoints (case_id, step, node, state) VALUES (?, ?, ?, ?)",
                (case_id, state["step"], node, json.dumps(state, sort_keys=True)))
            for who, fact_type, payload in facts:
                body = {"subject": case_id, "step": state["step"], **payload}
                if fact_type in STEP_FACTS:
                    # The step's entry carries the hash of the state it left behind,
                    # so a checkpoint edited later no longer matches the ledger.
                    body["state_hash"] = hash_obj(state)
                tx.append(sign_submission(self.keydir, who, fact_type, body, [case_id]))
            if self.crash_before_commit and self.crash_before_commit(node, state["step"]):
                raise RuntimeError(f"process died after node {node}, before commit")

    # ----------------------------------------------------------------- start

    def start(self, case_id: str, documents: list[dict[str, Any]], units: tuple[str, ...],
              models: dict[str, str] | None = None) -> dict[str, Any]:
        if self.ledger.query("SELECT 1 FROM runtime_cases WHERE case_id = ?", (case_id,)):
            raise RuntimeRefused(f"case {case_id} already exists")
        pins: dict[str, Any] = {"models": models or {}}
        for unit in units:
            head = self.channel_head(unit)
            pins[unit] = head["current"]
            pins[f"{unit}:manifest"] = head["manifest_ref"]
        refs = [self.vault.put(case_id, f"doc-{i}", doc) for i, doc in enumerate(documents)]
        state = {"case_id": case_id, "step": 0, "node": self.graph.start, "status": "running",
                 "docs": refs}
        self._commit(case_id, state, "start",
                     [(ORCHESTRATOR, "case.started", {"graph": self.graph.id, "pins": pins,
                                                      "documents": len(refs)})],
                     extra_sql=[("INSERT INTO runtime_cases (case_id, graph, pins) VALUES (?, ?, ?)",
                                 (case_id, self.graph.id, json.dumps(pins, sort_keys=True)))])
        return state

    # ------------------------------------------------------------------ step

    def _apply(self, node: Node, state: dict[str, Any], result: NodeResult) -> dict[str, Any]:
        for key in result.updates:
            if key not in node.writes:
                raise ContractViolation(
                    f"node {node.id} wrote '{key}', which its contract does not declare")
            owner = self.graph.regulated.get(key)
            if owner and owner != node.id:
                raise ContractViolation(
                    f"node {node.id} wrote the regulated field '{key}'; only {owner} may")
        for who, _fact_type, _payload in result.facts:
            if who != ORCHESTRATOR and who not in node.acts_as:
                raise ContractViolation(f"node {node.id} tried to act as {who}")
        return {**state, **result.updates, "step": state["step"] + 1,
                "node": result.next, "status": result.status}

    def step(self, case_id: str) -> dict[str, Any]:
        state = self.state(case_id)
        if state["status"] != "running":
            return state
        node = self.graph.nodes[state["node"]]
        if node.kind == "human":
            waiting = {**state, "step": state["step"] + 1, "status": "waiting-human"}
            self._commit(case_id, waiting, node.id,
                         [(ORCHESTRATOR, "node.completed", {"node": node.id, "waiting": True})])
            return waiting
        ctx = Context(case_id, self.pins(case_id), self.vault, self.releases_dir, self.gateway,
                      self.policy, self.ledger)
        visible = {k: state[k] for k in ("case_id", *node.reads) if k in state}
        try:
            result = node.fn(ctx, visible)
            new = self._apply(node, state, result)
        except (ContractViolation, RuntimeRefused) as exc:
            # A case that cannot proceed stops, visibly, with the reason on record.
            halted = {**state, "step": state["step"] + 1, "status": "halted"}
            self._commit(case_id, halted, node.id,
                         [(ORCHESTRATOR, "case.halted", {"node": node.id, "reason": str(exc)})])
            return halted
        facts = list(result.facts)
        facts.append((ORCHESTRATOR, "node.completed", {"node": node.id}))
        if new["status"] == "completed":
            facts.append((ORCHESTRATOR, "case.completed", {"pins": ctx.pins}))
        self._commit(case_id, new, node.id, facts)
        return new

    def run(self, case_id: str, max_steps: int = 20) -> dict[str, Any]:
        state = self.state(case_id)
        for _ in range(max_steps):
            if state["status"] != "running":
                break
            state = self.step(case_id)
        return state

    def resume(self, case_id: str, reviewer: str, resolution: dict[str, Any]) -> dict[str, Any]:
        """A person resolves a case that was routed to them. Durable: any engine
        instance, after any restart, can take it from here."""
        state = self.state(case_id)
        if state["status"] != "waiting-human":
            raise RuntimeRefused(f"case {case_id} is {state['status']}, not waiting for a person")
        node = self.graph.nodes[state["node"]]
        ref = self.vault.put(case_id, "resolution", resolution)
        new = {**state, "step": state["step"] + 1, "status": "running",
               "resolution_ref": ref, "node": node.after}
        self._commit(case_id, new, node.id, [
            (reviewer, "review.resolved", {"resolution_ref": ref}),
            (ORCHESTRATOR, "node.completed", {"node": node.id, "resumed": True})])
        return new

    # --------------------------------------------------------------- migrate

    def migrate(self, case_id: str, unit: str, to_version: str) -> dict[str, Any]:
        """Move a running case to another version. Only by a recorded, approved migration."""
        state = self.state(case_id)
        if state["status"] in ("completed", "halted"):
            raise RuntimeRefused(f"case {case_id} is {state['status']}")
        approved = [e for e in self.ledger.find(fact_type="migration.approved", ref=case_id)
                    if e.payload.get("unit") == unit and e.payload.get("to_version") == to_version]
        if not approved:
            raise RuntimeRefused(
                f"no approved migration of {case_id} to {unit}@{to_version}; a case keeps the "
                "version it started with")
        if not (self.releases_dir / unit / to_version).is_dir():
            raise RuntimeRefused(f"{unit}@{to_version} is not a stored release")
        pins = self.pins(case_id)
        before = pins[unit]
        pins[unit] = to_version
        new = {**state, "step": state["step"] + 1}
        self._commit(case_id, new, "migrate", [
            (ORCHESTRATOR, "case.migrated", {"unit": unit, "from_version": before,
                                             "to_version": to_version,
                                             "approval": approved[-1].id})],
            extra_sql=[("UPDATE runtime_cases SET pins = ? WHERE case_id = ?",
                        (json.dumps(pins, sort_keys=True), case_id))])
        return new

    # ------------------------------------------------------------- reconcile

    def reconcile(self) -> list[str]:
        """Completeness: every checkpoint has its ledger entry and every entry its checkpoint."""
        rows = self.ledger.query("SELECT case_id, step FROM runtime_checkpoints")
        checkpoints = {(r[0], int(r[1])) for r in rows}
        recorded = {(e.payload["subject"], e.payload["step"]) for e in self.ledger.entries()
                    if e.fact_type in STEP_FACTS}
        problems = [f"{c}: step {s} is checkpointed with no ledger entry"
                    for c, s in sorted(checkpoints - recorded)]
        problems += [f"{c}: step {s} is in the ledger with no checkpoint"
                     for c, s in sorted(recorded - checkpoints)]
        return problems
