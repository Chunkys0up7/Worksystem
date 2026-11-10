"""A small world: a prepared scenario, releases made the proper way, and a runtime.

Used by the tests, the attack pack and the journey so that each starts from
releases that actually went through the harness: delivered, gated, approved by
two people, sealed. Nothing is placed in the release store by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import executors as registry
from harness.gateway import Gateway
from harness.release import Release, approve, release
from runtime.engine import Engine
from runtime.income import GRAPH
from runtime.vault import Vault

from .suite import Env, deliver, new_gateway

APPROVERS = ("approver-ben", "approver-cy")
LAUNCHER = "approver-ana"
MODELS = {"explainer": "scripted-explainer"}
UNITS = ("decision-income",)


def releases_dir(env: Env) -> Path:
    return env.workdir / "releases"


def deliver_and_release(env: Env, scenario, keydir: Path, executor: str = "recorded", *,
                        change_id: str | None = None, tag: str = "w1", tower=None,
                        approvers=APPROVERS) -> Release:
    """One change, all the way: work order, gates, two approvals, sealed release."""
    adapter = registry.load(executor, env.scenario_dir, env.policy)
    order, run = deliver(env, scenario, adapter, keydir, with_canary=adapter.kind != "recorded",
                         gateway=new_gateway(env), launched_by=LAUNCHER, tower=tower,
                         change_id=change_id, workdir=env.workdir / tag)
    if run.outcome not in ("held-for-approval", "passed"):
        raise RuntimeError(f"{order.change_id} was {run.outcome}: {run.steps[-1]}")
    for person in approvers:
        approve(env.ledger, keydir, person, order.change_id)
    return release(order.change_id, ledger=env.ledger, keydir=keydir, policy=env.policy,
                   code_root=order.worktree, releases_dir=releases_dir(env))


def make_engine(env: Env, keydir: Path, *, adapters: dict[str, Any] | None = None,
                crash: Callable[[str, int], bool] | None = None, graph=GRAPH,
                gateway: Gateway | None = None) -> Engine:
    return Engine(graph, ledger=env.ledger, keydir=keydir, policy=env.policy,
                  vault=Vault(env.workdir / "vault"), releases_dir=releases_dir(env),
                  gateway=gateway or new_gateway(env, adapters), crash_before_commit=crash)


def advance(env: Env, scenario, base_root: Path, tag: str) -> Env:
    """Main moves forward: the repository as it stands in a later scenario, with
    the same ledger, the same release store and the same vault as before."""
    from dataclasses import replace

    from harness.knowledge import load_knowledge
    from harness.policy import load_policy

    from .scenario import prepare

    root = prepare(scenario, base_root, env.workdir / tag / "repo")
    return replace(env, root=root, policy=load_policy(root), knowledge=load_knowledge(root),
                   scenario_dir=root / "conformance" / "scenarios" / scenario.id)
