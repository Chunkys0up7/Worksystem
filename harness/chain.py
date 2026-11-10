"""The ID chain, walked in both directions from git and the ledger.

forward:  where is this decision implemented, and is it all passing?
why:      why does this code look like this?

Links that later stages add (spec, change trailers) are shown as open, with
the stage that closes them. An open link is stated, never hidden.
"""

from __future__ import annotations

from typing import Any

from .knowledge import Knowledge, SkillInvalid, verify
from .ledger import Ledger
from .policy import Policy
from .tiering import classify
from .tiering.rulebook import TierCheckFailed, glob_to_regex


def _latest_check(ledger: Ledger | None, check_id: str) -> dict[str, Any] | None:
    if ledger is None:
        return None
    found = ledger.find(fact_type="check.result", ref=check_id)
    if not found:
        return None
    entry = found[-1]
    return {
        "ledger": entry.id,
        "ts": entry.ts,
        "outcome": entry.payload["outcome"],
        "skill": entry.payload["skill"],
        "governed_code_hash": entry.payload["governed_code_hash"][:12],
    }


def forward(decision_id: str, knowledge: Knowledge, policy: Policy,
            ledger: Ledger | None) -> dict[str, Any]:
    decision = knowledge.decisions.get(decision_id)
    if decision is None:
        raise KeyError(f"{decision_id} is not in the decision register")
    out: dict[str, Any] = {
        "decision": decision_id,
        "title": decision.get("title", ""),
        "moments": sorted(
            m for m, v in knowledge.moments.items() if decision_id in v.get("obligations", [])
        ),
        "cards": sorted(
            c for c, v in knowledge.cards.items() if decision_id in v.get("implements", [])
        ),
        "skills": [],
    }
    for skill in knowledge.skills.values():
        if decision_id not in skill.implements:
            continue
        try:
            release = verify(skill, policy)
            state = f"released, signature verified, content {release['content_hash'][:12]}"
        except SkillInvalid as exc:
            state = f"NOT VERIFIED: {exc}"
        entry = next((s for s in policy.skill_map if s["id"] == skill.id), {})
        out["skills"].append(
            {
                "skill": skill.ref,
                "state": state,
                "governs": entry.get("governs", []),
                "checks": [
                    {
                        "check": check["id"],
                        "covers": check["covers"],
                        "latest": _latest_check(ledger, check["id"]),
                    }
                    for check in skill.checks
                ],
                "pending": skill.pending,
                "open_links": [
                    "spec citing this decision (stage 4)",
                    "change trailers with spec ID and skill versions loaded (stage 4)",
                ],
            }
        )
    return out


def why(path: str, knowledge: Knowledge, policy: Policy) -> dict[str, Any]:
    try:
        decision = classify([path], policy)
        tier, reasons = decision.tier, decision.findings[0].reasons
    except TierCheckFailed as exc:
        tier, reasons = "check-failed", [str(exc)]
    out: dict[str, Any] = {"path": path, "tier": tier, "tier_reasons": reasons, "skills": []}
    for entry in policy.skill_map:
        if not any(glob_to_regex(g).match(path) for g in entry.get("governs", [])):
            continue
        skill = knowledge.skills.get(entry["id"])
        if skill is None:
            continue
        out["skills"].append(
            {
                "skill": skill.ref,
                "owner": entry.get("owner", ""),
                "cards": {
                    c: knowledge.cards[c].get("title", "") for c in skill.cards
                    if c in knowledge.cards
                },
                "decisions": {
                    d: knowledge.decisions[d].get("title", "") for d in skill.implements
                    if d in knowledge.decisions
                },
                "consuming_nodes": entry.get("consuming_nodes", []),
            }
        )
    return out


def render_forward(data: dict[str, Any]) -> str:
    lines = [f"{data['decision']}  {data['title']}"]
    if data["moments"]:
        lines.append(f"  moments: {', '.join(data['moments'])}")
    lines.append(f"  cards:   {', '.join(data['cards']) or 'none'}")
    if not data["skills"]:
        lines.append("  NO SKILL IMPLEMENTS THIS DECISION")
    for skill in data["skills"]:
        lines.append(f"  skill {skill['skill']}")
        lines.append(f"    {skill['state']}")
        lines.append(f"    governs: {', '.join(skill['governs'])}")
        for check in skill["checks"]:
            latest = check["latest"]
            if latest:
                status = (f"{latest['outcome'].upper()} in {latest['ledger']} at "
                          f"{latest['ts']} against code {latest['governed_code_hash']}")
            else:
                status = "NO RESULT IN THE LEDGER"
            lines.append(f"    {check['check']} covers {', '.join(check['covers'])}: {status}")
        for pending in skill["pending"]:
            lines.append(
                f"    open: {pending['card']} ({pending['reason']}; stage {pending['due_stage']})"
            )
        for link in skill["open_links"]:
            lines.append(f"    open: {link}")
    return "\n".join(lines)


def render_why(data: dict[str, Any]) -> str:
    lines = [f"{data['path']}  Tier {data['tier']}"]
    for reason in data["tier_reasons"]:
        lines.append(f"  because {reason}")
    if not data["skills"]:
        lines.append("  no skill governs this path")
    for skill in data["skills"]:
        lines.append(f"  governed by {skill['skill']} (owner {skill['owner']})")
        for card, title in skill["cards"].items():
            lines.append(f"    {card}  {title}")
        for decision, title in skill["decisions"].items():
            lines.append(f"    implements {decision}  {title}")
        for node in skill["consuming_nodes"]:
            flag = "regulated" if node.get("regulated") else "not regulated"
            lines.append(f"    loaded by {node['node']} ({flag})")
    return "\n".join(lines)
