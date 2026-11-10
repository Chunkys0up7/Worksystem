"""The model gateway: the only route from anything in this build to a model.

It holds the controls that must not live inside an executor or an agent node:

- the pin: a model id that is not in policy/models.yaml is refused
- the role: a model approved as an author is not thereby approved as a reviewer
- the budget: calls, tokens and wall time per session group, failing closed
- the record: what was used, written to the ledger under the gateway identity

Adapters sit behind it. The harness never imports one; they are handed in at
the edge. An adapter is anything with `complete(messages, tools) -> dict`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .policy import Policy

GATEWAY_IDENTITY = "model-gateway"


class GatewayRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class BudgetExceeded(GatewayRefused):
    pass


class ModelAdapter(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Return {"text": str, "tool_calls": [{"name": str, "args": dict}]}."""


def estimate_tokens(obj: Any) -> int:
    """A rough count, about four characters a token. An adapter that knows the
    real figure can return it as `usage` and that is used instead."""
    return max(1, len(json.dumps(obj, default=str)) // 4)


@dataclass
class Usage:
    model: str
    family: str
    kind: str
    role: str
    calls: int = 0
    tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "family": self.family, "kind": self.kind,
                "role": self.role, "calls": self.calls, "tokens": self.tokens}


@dataclass
class Budget:
    """Shared by every session opened for one piece of work."""

    max_calls: int
    max_tokens: int
    max_seconds: float
    started: float
    calls: int = 0
    tokens: int = 0


class Session:
    def __init__(self, adapter: ModelAdapter, usage: Usage, budget: Budget,
                 clock: Callable[[], float]) -> None:
        self._adapter, self.usage, self._budget, self._clock = adapter, usage, budget, clock

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        budget = self._budget
        if budget.calls + 1 > budget.max_calls:
            raise BudgetExceeded("budget-calls", f"more than {budget.max_calls} model calls")
        if self._clock() - budget.started > budget.max_seconds:
            raise BudgetExceeded("budget-time", f"more than {budget.max_seconds}s")
        asked = estimate_tokens(messages)
        if budget.tokens + asked > budget.max_tokens:
            raise BudgetExceeded("budget-tokens", f"more than {budget.max_tokens} tokens")
        reply = self._adapter.complete(messages, tools or [])
        used = (reply.get("usage") or {}).get("tokens") or asked + estimate_tokens(reply)
        budget.calls += 1
        budget.tokens += used
        self.usage.calls += 1
        self.usage.tokens += used
        return {"text": reply.get("text", ""), "tool_calls": list(reply.get("tool_calls") or [])}


@dataclass
class Gateway:
    policy: Policy
    adapters: dict[str, ModelAdapter]
    clock: Callable[[], float] = time.monotonic
    caps: dict[str, int] | None = None
    sessions: list[Session] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    _budget: Budget | None = None

    def _the_budget(self) -> Budget:
        if self._budget is None:
            caps = {**(self.policy.model_caps or {}), **(self.caps or {})}
            self._budget = Budget(int(caps.get("max_calls", 30)),
                                  int(caps.get("max_tokens", 120000)),
                                  float(caps.get("max_seconds", 120)), self.clock())
        return self._budget

    def session(self, model_id: str, role: str, data_classes: tuple[str, ...] = ()) -> Session:
        """`data_classes` is what the caller will send. A model sees a class of
        data only if its pin lists it: the per-model data-path register."""
        pin = (self.policy.models or {}).get(model_id)
        try:
            if pin is None:
                raise GatewayRefused("unpinned-model", f"{model_id} is not in policy/models.yaml")
            if role not in pin["roles"]:
                raise GatewayRefused(
                    "model-not-approved-for-role", f"{model_id} is not approved as {role}")
            missing = [c for c in data_classes if c not in (pin.get("data_classes") or [])]
            if missing:
                raise GatewayRefused(
                    "model-not-approved-for-data",
                    f"{model_id} has no approved data path for {', '.join(missing)}")
            if model_id not in self.adapters:
                raise GatewayRefused("no-adapter", f"no adapter is registered for {model_id}")
        except GatewayRefused as exc:
            self.refusals.append(str(exc))
            raise
        session = Session(self.adapters[model_id],
                          Usage(model_id, pin["family"], pin.get("kind", "live"), role),
                          self._the_budget(), self.clock)
        self.sessions.append(session)
        return session

    def family_of(self, model_id: str) -> str | None:
        pin = (self.policy.models or {}).get(model_id)
        return pin["family"] if pin else None

    def usage(self) -> list[dict[str, Any]]:
        return [s.usage.as_dict() for s in self.sessions]

    def all_scripted(self) -> bool:
        return bool(self.sessions) and all(s.usage.kind == "scripted" for s in self.sessions)
