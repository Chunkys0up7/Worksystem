"""The income verification graph. SYNTHETIC.

    intake -> conflict -> calculate -> explain -> done
                     \\-> human review -/

Which node may do what:

    conflict   deterministic. PROC-0001: if two documents disagree on base pay
               or pay frequency, do not choose and do not calculate; route to a
               person. It reads the figures only. A note in a document is never
               read here, so a document cannot talk its way past the rule.
    calculate  deterministic. Calls the decision service at the version pinned
               on the case. The only node that may write the result.
    explain    an agent. A model drafts the explanation. The draft is checked
               against the receipt: any amount it states must be one the service
               returned, and it must name the cards applied. If not, the draft
               is discarded for a template and the trip is recorded.
"""

from __future__ import annotations

import re
from typing import Any

from harness.gateway import GatewayRefused

from .engine import Context, Graph, Node, NodeResult, RuntimeRefused

UNIT = "decision-income"
SERVICE = "svc-decision-income"
EXPLAINER = "rt-explainer"
COMPARED = ("base_per_period", "frequency")
INPUT_FIELDS = ("base_per_period", "frequency", "variable_history_months",
                "variable_total_in_window", "variable_total_recent")
AMOUNT = re.compile(r"\d[\d,]*\.\d{2}")


def intake(ctx: Context, state: dict[str, Any]) -> NodeResult:
    for ref in state["docs"]:
        ctx.vault.get(ref)  # every document must be there
    if not state["docs"]:
        raise RuntimeRefused("a case needs at least one document")
    return NodeResult({"documents_checked": len(state["docs"])}, next="conflict")


def conflict(ctx: Context, state: dict[str, Any]) -> NodeResult:
    documents = [ctx.vault.get(ref) for ref in state["docs"]]
    differing = sorted(f for f in COMPARED if len({str(d.get(f)) for d in documents}) > 1)
    if differing:
        return NodeResult(
            {"conflict": True, "conflict_fields": differing},
            facts=[("rt-orchestrator", "case.routed",
                    {"to": "human", "card": "PROC-0001", "fields": differing})],
            next="human_review")
    return NodeResult({"conflict": False, "conflict_fields": []}, next="calculate")


def calculate(ctx: Context, state: dict[str, Any]) -> NodeResult:
    if state.get("conflict") and not state.get("resolution_ref"):
        raise RuntimeRefused("PROC-0001: a conflicting case is not calculated before a person resolves it")
    source = (ctx.vault.get(state["resolution_ref"]) if state.get("resolution_ref")
              else ctx.vault.get(state["docs"][0]))
    inputs = {k: source.get(k) for k in INPUT_FIELDS}
    result = ctx.decision_service(UNIT, inputs)
    if "refused" in result:
        raise RuntimeRefused(f"the decision service refused the inputs: {result['refused']}")
    inputs_ref = ctx.vault.put(ctx.case_id, "inputs", inputs)
    result_ref = ctx.vault.put(ctx.case_id, "result", result)
    receipt = {"unit": UNIT, "version": ctx.pins[UNIT], "manifest": ctx.pins[f"{UNIT}:manifest"],
               "inputs_ref": inputs_ref, "result_ref": result_ref,
               "cards_applied": result["cards_applied"]}
    return NodeResult(
        {"inputs_ref": inputs_ref, "result_ref": result_ref,
         "cards_applied": result["cards_applied"], "decided_by": f"{UNIT}@{ctx.pins[UNIT]}"},
        facts=[(SERVICE, "decision.made", receipt)], next="explain")


def template(result: dict[str, Any]) -> str:
    return (f"Qualifying monthly income is {result['qualifying_monthly']}: base income "
            f"{result['base_monthly']} plus eligible variable income {result['variable_monthly']}. "
            f"Rules applied: {', '.join(result['cards_applied'])}.")


def guard(text: str, result: dict[str, Any]) -> str | None:
    """Why a drafted explanation cannot be used, or None if it can."""
    allowed = {result["qualifying_monthly"], result["base_monthly"], result["variable_monthly"]}
    stated = {a.replace(",", "") for a in AMOUNT.findall(text)}
    if stated - allowed:
        return f"states an amount the decision service did not return ({len(stated - allowed)} such)"
    if result["qualifying_monthly"] not in stated:
        return "does not state the qualifying figure"
    missing = [c for c in result["cards_applied"] if c not in text]
    if missing:
        return f"does not name {', '.join(missing)}"
    return None


def explain(ctx: Context, state: dict[str, Any]) -> NodeResult:
    result = ctx.vault.get(state["result_ref"])
    model = ctx.pins["models"].get("explainer")
    text, problem, used = None, None, None
    if ctx.gateway is not None and model:
        try:
            session = ctx.gateway.session(model, "explainer",
                                          data_classes=("consumer-personal", "credit-decision"))
            reply = session.complete([
                {"role": "system", "content": "Explain this income decision to the applicant in "
                                              "two sentences. State only the figures given."},
                {"role": "user", "content": {"result": result}}])
            text, used = str(reply.get("text", "")), session.usage.kind
            problem = guard(text, result)
        except GatewayRefused as exc:
            problem = f"no model: {exc}"
    else:
        problem = "no model configured"
    facts = []
    if problem:
        facts.append(("rt-orchestrator", "guard.tripped", {"node": "explain", "reason": problem}))
        text = template(result)
    ref = ctx.vault.put(ctx.case_id, "explanation-text", text)
    facts.append((EXPLAINER, "agent.output",
                  {"node": "explain", "model": model, "model_kind": used,
                   "explanation_ref": ref, "used": "template" if problem else "model draft"}))
    return NodeResult({"explanation_ref": ref, "explained_by": "template" if problem else model},
                      facts=facts, next=None, status="completed")


GRAPH = Graph(
    id="income-verification",
    start="intake",
    regulated={"result_ref": "calculate", "cards_applied": "calculate", "decided_by": "calculate"},
    nodes={
        "intake": Node("intake", "deterministic", ("docs",), ("documents_checked",), intake),
        "conflict": Node("conflict", "deterministic", ("docs",), ("conflict", "conflict_fields"),
                         conflict),
        "human_review": Node("human_review", "human", ("docs", "conflict_fields"),
                             ("resolution_ref",), after="calculate"),
        "calculate": Node("calculate", "deterministic", ("docs", "conflict", "resolution_ref"),
                          ("inputs_ref", "result_ref", "cards_applied", "decided_by"),
                          calculate, acts_as=(SERVICE,)),
        "explain": Node("explain", "agent", ("result_ref",), ("explanation_ref", "explained_by"),
                        explain, acts_as=(EXPLAINER,)),
    },
)
